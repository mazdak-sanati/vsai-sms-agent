from __future__ import annotations

from time_report_agent.cli import _ask_to_send


def test_ask_to_send_yes(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert _ask_to_send(1) is True


def test_ask_to_send_no(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert _ask_to_send(1) is False


def test_ask_to_send_zero_eligible() -> None:
    assert _ask_to_send(0) is False
