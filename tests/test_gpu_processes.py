"""游戏白名单和只读进程采集；不启动或结束真实游戏进程。"""

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from nova.gpu import processes
from nova.gpu.processes import ProcessReadError, list_process_names, normalize_game_names


def install_tasklist(monkeypatch, output, *, returncode=0):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=output, stderr=b"PRIVATE_STDERR")

    monkeypatch.setattr(processes.subprocess, "run", run)
    monkeypatch.setattr(processes, "_oem_encoding", lambda: "cp936")
    monkeypatch.setattr(processes.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    return calls


def test_white_list_normalization_deduplicates_without_guessing():
    assert normalize_game_names(
        ["  Training Test Game.EXE ", "training test game.exe", "虚构.exe"]
    ) == [
        "training test game.exe",
        "虚构.exe",
    ]
    assert normalize_game_names([]) == []


@pytest.mark.parametrize(
    "value",
    [
        "game.exe",
        ("game.exe",),
        [None],
        [""],
        ["   "],
        ["game"],
        [".exe"],
        ["C:\\Games\\game.exe"],
        ["./game.exe"],
        ["game*.exe"],
        ["game?.exe"],
        ['"game.exe"'],
        ["game|fake.exe"],
        ["game\x00.exe"],
        ["game\n.exe"],
        ["game\x7f.exe"],
        ["a" * 252 + ".exe"],
    ],
)
def test_invalid_white_list_raises_fixed_error_without_echoing_input(value):
    with pytest.raises(ValueError) as caught:
        normalize_game_names(value)
    assert "C:\\Games" not in str(caught.value)
    assert "fake" not in str(caught.value)


def test_reads_csv_oem_names_and_uses_bounded_hidden_command(monkeypatch):
    text = (
        '"System Idle Process","0","Services","0","8 K"\r\n'
        '"Synthetic Test.EXE","1234","Console","1","12,345 K"\r\n'
        '"Synthetic Test.exe","1235","Console","1","12,345 K"\r\n'
        '"虚构游戏.exe","1236","Console","1","12,345 K"\r\n'
    )
    calls = install_tasklist(monkeypatch, text.encode("cp936"))
    assert asyncio.run(list_process_names()) == {"synthetic test.exe", "虚构游戏.exe"}
    command, options = calls[0]
    assert command == ["C:\\Windows\\System32\\tasklist.exe", "/FO", "CSV", "/NH"]
    assert options["timeout"] == 5
    assert options["capture_output"] is True
    assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert not options.get("shell", False)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-le"])
def test_known_unicode_output_encodings_are_parsed(monkeypatch, encoding):
    text = '"Synthetic.exe","17","Console","1","1 K"\r\n'
    install_tasklist(monkeypatch, text.encode(encoding))
    assert asyncio.run(list_process_names()) == {"synthetic.exe"}


@pytest.mark.parametrize(
    "output",
    [
        b"",
        b"INFO: No tasks are running which match the specified criteria.\r\n",
        b'"System","4","Services","0","8 K"\r\n',
        b'"Synthetic.exe","not-a-pid","Console","1","8 K"\r\n',
        b'"Synthetic.exe","0","Console","1","8 K"\r\n',
        b'"Synthetic.exe","1"\r\n',
        b'"Synthetic.exe","17","Console","1","8 K\r\n',
        b'"C:\\private\\Synthetic.exe","17","Console","1","8 K"\r\n',
        b'"Synthetic.exe","17","Console","1","\x81"\r\n',
    ],
)
def test_unreliable_snapshot_never_becomes_an_empty_set(monkeypatch, output):
    install_tasklist(monkeypatch, output)
    with pytest.raises(ProcessReadError) as caught:
        asyncio.run(list_process_names())
    assert "PRIVATE_STDERR" not in str(caught.value)
    assert "private" not in str(caught.value)


def test_nonzero_exit_is_failure_even_when_stdout_looks_valid(monkeypatch):
    install_tasklist(monkeypatch, b'"Synthetic.exe","17","Console","1","8 K"', returncode=1)
    with pytest.raises(ProcessReadError):
        asyncio.run(list_process_names())


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("private path"), subprocess.TimeoutExpired("private command", 5)]
)
def test_missing_tool_or_timeout_returns_safe_failure(monkeypatch, failure):
    install_tasklist(monkeypatch, b"unused")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(processes.subprocess, "run", fail)
    with pytest.raises(ProcessReadError) as caught:
        asyncio.run(list_process_names())
    assert "private" not in str(caught.value)


def test_unsupported_platform_is_not_reported_as_no_games(monkeypatch):
    calls = install_tasklist(monkeypatch, b"unused")
    monkeypatch.setattr(processes.sys, "platform", "linux")
    with pytest.raises(ProcessReadError):
        asyncio.run(list_process_names())
    assert calls == []


def test_missing_system_root_returns_safe_read_failure(monkeypatch):
    calls = install_tasklist(monkeypatch, b"unused")
    monkeypatch.delenv("SystemRoot")
    with pytest.raises(ProcessReadError) as caught:
        asyncio.run(list_process_names())
    assert "SystemRoot" not in str(caught.value)
    assert calls == []
