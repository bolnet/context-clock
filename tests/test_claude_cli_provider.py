"""Claude via the Claude Code CLI (`claude -p`) — subscription auth, NO API key.

Tested with a mocked subprocess — no real CLI call.
"""

import json
import subprocess

from context_clock.claude_cli_provider import ClaudeCliProvider


class _FakeProc:
    def __init__(self, payload):
        self.stdout = json.dumps(payload)
        self.stderr = ""
        self.returncode = 0


def _mock_run(monkeypatch, payload, captured):
    def fake(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _FakeProc(payload)

    monkeypatch.setattr(subprocess, "run", fake)


class TestClaudeCliProvider:
    def test_parses_result_and_usage(self, monkeypatch):
        _mock_run(monkeypatch, {
            "result": "ka5cd6",
            "usage": {"input_tokens": 120, "output_tokens": 4},
        }, {})
        p = ClaudeCliProvider(model="haiku")
        c = p.complete([{"role": "system", "content": "sys"},
                        {"role": "user", "content": "code?"}], max_tokens=16)
        assert c.text == "ka5cd6"
        assert c.prompt_tokens == 120
        assert c.completion_tokens == 4

    def test_invokes_claude_with_model_and_json(self, monkeypatch):
        cap = {}
        _mock_run(monkeypatch, {"result": "ok", "usage": {}}, cap)
        ClaudeCliProvider(model="haiku").complete([{"role": "user", "content": "hi"}])
        cmd = cap["cmd"]
        assert cmd[0].endswith("claude")
        assert "-p" in cmd
        assert "--model" in cmd and "haiku" in cmd
        assert "--output-format" in cmd and "json" in cmd

    def test_system_message_goes_to_append_system_prompt(self, monkeypatch):
        cap = {}
        _mock_run(monkeypatch, {"result": "ok", "usage": {}}, cap)
        ClaudeCliProvider(model="haiku").complete([
            {"role": "system", "content": "BE TERSE"},
            {"role": "user", "content": "hi"},
        ])
        cmd = cap["cmd"]
        assert "--append-system-prompt" in cmd
        i = cmd.index("--append-system-prompt")
        assert "BE TERSE" in cmd[i + 1]
        assert any("hi" in str(a) for a in cmd)  # user content reaches the prompt
