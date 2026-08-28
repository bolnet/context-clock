"""Sandboxed agent tools — the workspace boundary is a security boundary.

The caller is a language model following a benchmark prompt, so every path it
supplies is untrusted input.
"""

import pytest

from context_clock.cachecost.tools import Workspace, dispatch


@pytest.fixture
def workspace(tmp_path):
    return Workspace(tmp_path / "ws")


class TestSandbox:
    @pytest.mark.parametrize(
        "path", ["../escape.py", "../../etc/passwd.py", "sub/../../out.py"]
    )
    def test_traversal_is_rejected(self, workspace, path):
        with pytest.raises(ValueError, match="escapes the workspace"):
            workspace.write_file(path, "x = 1")

    def test_absolute_path_is_rejected(self, workspace, tmp_path):
        with pytest.raises(ValueError, match="escapes the workspace"):
            workspace.write_file(str(tmp_path / "elsewhere.py"), "x = 1")

    def test_disallowed_extension_is_rejected(self, workspace):
        with pytest.raises(ValueError, match="disallowed extension"):
            workspace.write_file("payload.sh", "rm -rf /")

    def test_empty_path_is_rejected(self, workspace):
        with pytest.raises(ValueError, match="must not be empty"):
            workspace.write_file("   ", "x = 1")

    def test_nested_write_inside_workspace_is_allowed(self, workspace):
        workspace.write_file("pkg/mod.py", "x = 1")
        assert workspace.read_file("pkg/mod.py").text == "x = 1"


class TestFileTools:
    def test_write_then_read_roundtrips(self, workspace):
        workspace.write_file("a.py", "value = 42\n")
        assert workspace.read_file("a.py").text == "value = 42\n"

    def test_write_overwrites(self, workspace):
        workspace.write_file("a.py", "old")
        workspace.write_file("a.py", "new")
        assert workspace.read_file("a.py").text == "new"

    def test_missing_file_is_an_error_not_an_exception(self, workspace):
        result = workspace.read_file("nope.py")
        assert result.is_error is True
        assert "No such file" in result.text

    def test_list_files_is_sorted_and_skips_pycache(self, workspace):
        workspace.write_file("b.py", "1")
        workspace.write_file("a.py", "1")
        (workspace.root / "__pycache__").mkdir()
        (workspace.root / "__pycache__" / "junk.pyc").write_text("x")
        assert workspace.list_files().text == "a.py\nb.py"

    def test_empty_workspace_lists_clearly(self, workspace):
        assert "empty" in workspace.list_files().text

    def test_long_reads_are_truncated(self, workspace):
        workspace.write_file("big.py", "#" * 60_000)
        assert "truncated" in workspace.read_file("big.py").text


class TestRunTests:
    def test_passing_suite_reports_exit_zero(self, workspace):
        workspace.write_file("test_ok.py", "def test_ok():\n    assert True\n")
        assert workspace.run_tests().text.startswith("exit code 0")
        assert workspace.tests_pass() is True

    def test_failing_suite_is_data_not_an_error(self, workspace):
        workspace.write_file("test_bad.py", "def test_bad():\n    assert False\n")
        result = workspace.run_tests()
        assert result.is_error is False  # a red suite is information for the model
        assert not result.text.startswith("exit code 0")
        assert workspace.tests_pass() is False


class TestDispatch:
    def test_routes_a_known_tool(self, workspace):
        result = dispatch(workspace, "write_file", {"path": "a.py", "content": "x = 1"})
        assert result.is_error is False
        assert workspace.read_file("a.py").text == "x = 1"

    def test_unknown_tool_is_reported_not_raised(self, workspace):
        result = dispatch(workspace, "rm_rf", {})
        assert result.is_error is True
        assert "Unknown tool" in result.text

    def test_missing_argument_is_reported_not_raised(self, workspace):
        result = dispatch(workspace, "write_file", {"path": "a.py"})
        assert result.is_error is True
        assert "Missing required argument" in result.text

    def test_sandbox_violation_becomes_a_tool_error(self, workspace):
        # The loop must survive this and let the model correct itself.
        result = dispatch(workspace, "write_file", {"path": "../x.py", "content": ""})
        assert result.is_error is True
        assert "escapes the workspace" in result.text
