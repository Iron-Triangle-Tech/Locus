"""Dedicated coverage for :func:`endpoint.ui.tui.parse_slash`.

The renderers live in :mod:`tests.endpoint.test_tui_render`; this file isolates
the slash-command parser so future command surface changes have a focused home.
"""

from __future__ import annotations

import pytest

from endpoint.ui.tui import parse_slash


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/new", ("new", None)),
        ("/new please", ("new", None)),
        ("/exit", ("exit", None)),
        ("/quit", ("exit", None)),
        ("/clear", ("clear", None)),
        ("/help", ("help", None)),
        ("/help tools", ("help", "tools")),
        ("/help me understand", ("help", "me understand")),
        ("/frobnicate x", ("none", None)),
        ("hello there", ("none", None)),
        ("   ", ("none", None)),
        ("  /exit  ", ("exit", None)),
        ("/NEW", ("new", None)),
        ("/Exit", ("exit", None)),
        ("/CLEAR", ("clear", None)),
    ],
)
def test_parse_slash_cases(line: str, expected: tuple[str, str | None]) -> None:
    assert parse_slash(line) == expected


class TestParseSlashExtras:
    def test_takes_only_first_token_as_command(self) -> None:
        assert parse_slash("/help extra tokens here") == ("help", "extra tokens here")

    def test_slash_alone_is_unknown(self) -> None:
        assert parse_slash("/") == ("none", None)

    def test_tab_separated_argument(self) -> None:
        assert parse_slash("/help\tthing") == ("help", "thing")

    def test_only_argument_after_command_returned(self) -> None:
        # /new drops any trailing argument intentionally — it has no arg.
        assert parse_slash("/new ignored") == ("new", None)

    def test_help_argument_is_whitespace_preserved(self) -> None:
        # Internal whitespace in the help arg is kept as-is (only stripped ends).
        assert parse_slash("  /help   spaced   out   ") == ("help", "spaced   out")
