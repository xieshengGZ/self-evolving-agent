from app import actions, config


def test_shell_runs_and_returns_stdout():
    result = actions.do_shell("printf hello", timeout=10)
    assert result["result_of"] == "shell"
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"


def test_dangerous_command_is_rejected():
    result = actions.do_shell("shutdown -h now", timeout=10)
    assert "error" in result
    assert "破坏性" in result["error"]


def test_cmd_timeout_symbol_exists():
    assert isinstance(config.AGENT_CMD_TIMEOUT, int)
    assert config.AGENT_CMD_TIMEOUT > 0


def test_write_then_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUPS_DIR", str(tmp_path / "backups"))
    target = tmp_path / "nested" / "file.txt"

    written = actions.do_write_file(str(target), "内容 abc")
    assert written["result_of"] == "write_file"
    assert written["bytes_written"] > 0

    read = actions.do_read_file(str(target), max_bytes=3)
    assert read["result_of"] == "read_file"
    assert read["truncated"] is True
    assert len(read["content"].encode("utf-8")) <= 3


def test_duplicate_dispatchers_were_removed():
    assert not hasattr(actions, "execute_action")
    assert not hasattr(actions, "parse_action")
