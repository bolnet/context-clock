"""Sandboxed file + test tools for the benchmark agent.

The agent does real work — it writes real files and runs a real test suite —
because a synthetic workload would not produce the thing under measurement:
long agentic turns whose tool results pile up in the conversation.

Every path is confined to one workspace directory. The agent is a language
model following a benchmark prompt, not a trusted caller, so traversal is
rejected rather than normalised, and the test runner is a fixed argv with a
timeout — never a shell string.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_READ_BYTES = 40_000
TEST_TIMEOUT_SECONDS = 120

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "write_file",
        "description": "Create or overwrite a file in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "content": {"type": "string", "description": "Full file contents."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List every file currently in the workspace.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_tests",
        "description": "Run pytest over the workspace and return the output.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


class Workspace:
    """A directory the agent may read and write, and nothing outside it."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative: str) -> Path:
        """Resolve a workspace-relative path, refusing anything that escapes."""
        if not relative or not relative.strip():
            raise ValueError("path must not be empty")
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"path {relative!r} escapes the workspace")
        if candidate.suffix not in (".py", ".txt", ".md", ".cfg", ".toml", ".ini"):
            raise ValueError(f"path {relative!r} has a disallowed extension")
        return candidate

    def write_file(self, path: str, content: str) -> ToolResult:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        lines = content.count("\n") + 1
        return ToolResult(f"Wrote {path} ({lines} lines, {len(content)} bytes).")

    def read_file(self, path: str) -> ToolResult:
        target = self._resolve(path)
        if not target.exists():
            return ToolResult(f"No such file: {path}", is_error=True)
        text = target.read_text()
        if len(text) > MAX_READ_BYTES:
            text = text[:MAX_READ_BYTES] + f"\n... truncated at {MAX_READ_BYTES} bytes"
        return ToolResult(text)

    def list_files(self) -> ToolResult:
        found = sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )
        return ToolResult("\n".join(found) if found else "(workspace is empty)")

    def run_tests(self) -> ToolResult:
        """Run pytest in the workspace. A failing suite is data, not an error."""
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=TEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                f"Test run exceeded {TEST_TIMEOUT_SECONDS}s and was killed. "
                "Likely an infinite loop.",
                is_error=True,
            )
        output = (proc.stdout + proc.stderr).strip()
        if len(output) > MAX_READ_BYTES:
            output = output[-MAX_READ_BYTES:]
        return ToolResult(f"exit code {proc.returncode}\n{output}")

    def tests_pass(self) -> bool:
        """Ground truth for 'the task is done' — independent of what the agent says."""
        result = self.run_tests()
        return result.text.startswith("exit code 0")


def dispatch(workspace: Workspace, name: str, arguments: dict) -> ToolResult:
    """Route one tool call. An unknown tool or bad argument is reported, never raised.

    The loop must survive a malformed call: turning it into a tool_result the
    model can read is what lets the session continue and keeps the measurement
    running.
    """
    try:
        if name == "write_file":
            return workspace.write_file(arguments["path"], arguments["content"])
        if name == "read_file":
            return workspace.read_file(arguments["path"])
        if name == "list_files":
            return workspace.list_files()
        if name == "run_tests":
            return workspace.run_tests()
        return ToolResult(f"Unknown tool: {name}", is_error=True)
    except KeyError as exc:
        return ToolResult(f"Missing required argument {exc} for {name}", is_error=True)
    except (ValueError, OSError) as exc:
        return ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
