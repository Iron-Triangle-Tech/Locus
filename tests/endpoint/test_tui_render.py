"""Pure-presenter tests for endpoint.ui.tui frame renderers.

The slash parser has its own dedicated coverage in
:mod:`tests.endpoint.test_tui_slash`; this file covers the renderer helpers
(``render_frame`` + the per-frame ``render_*`` functions) only.
"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from endpoint.ui.tui import render_frame, render_thinking, render_tool_call
from shared.protocol import (
    ErrorEvent,
    FinalEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)


def _panel_title(renderable: object) -> str:
    assert isinstance(renderable, Panel)
    return str(renderable.title)  # type: ignore[arg-type]


class TestFrameRenderers:
    def test_token_renders_as_plain_text(self) -> None:
        frame = TokenEvent(thread_id="t", delta="Hi")
        r = render_frame(frame)
        assert isinstance(r, Text)
        assert str(r) == "Hi"

    def test_final_renders_as_plain_text(self) -> None:
        frame = FinalEvent(thread_id="t", text="bye")
        r = render_frame(frame)
        assert isinstance(r, Text)
        assert str(r) == "bye"

    def test_thinking_panel_is_dim_bordered(self) -> None:
        frame = ThinkingEvent(thread_id="t", delta="reasoning here")
        r = render_frame(frame)
        assert isinstance(r, Panel)
        assert _panel_title(r) == "thinking"
        assert "reasoning here" in str(r.renderable)

    def test_tool_call_panel_names_tool_and_tag(self) -> None:
        frame = ToolCallEvent(
            thread_id="t",
            call_id="c1",
            name="get_weather",
            arguments={"city": "SF"},
            local=True,
        )
        r = render_frame(frame)
        assert isinstance(r, Panel)
        body = str(r.renderable)
        assert "get_weather" in body
        assert "core" in body
        assert "SF" in body

    def test_tool_call_endpoint_tag(self) -> None:
        frame = ToolCallEvent(
            thread_id="t",
            call_id="c1",
            name="run_local",
            arguments={},
            local=False,
        )
        r = render_frame(frame)
        body = str(r.renderable)
        assert "endpoint" in body

    def test_tool_result_ok_panel(self) -> None:
        frame = ToolResultEvent(thread_id="t", call_id="c1", ok=True, output="sunny")
        r = render_frame(frame)
        assert isinstance(r, Panel)
        assert "ok" in _panel_title(r)
        assert "sunny" in str(r.renderable)

    def test_tool_result_error_panel(self) -> None:
        frame = ToolResultEvent(
            thread_id="t", call_id="c1", ok=False, error="boom"
        )
        r = render_frame(frame)
        assert isinstance(r, Panel)
        assert "ERROR" in _panel_title(r)
        assert "boom" in str(r.renderable)

    def test_error_panel_fatal_marker(self) -> None:
        frame = ErrorEvent(thread_id="t", message="kaput", fatal=True)
        r = render_frame(frame)
        assert isinstance(r, Panel)
        assert _panel_title(r) == "FATAL"
        assert "kaput" in str(r.renderable)

    def test_error_panel_nonfatal(self) -> None:
        frame = ErrorEvent(thread_id="t", message="oops", fatal=False)
        r = render_frame(frame)
        assert _panel_title(r) == "error"

    def test_unknown_frame_renders_placeholder(self) -> None:
        r = render_frame(object())
        assert isinstance(r, Text)
        assert "unhandled frame" in str(r)

    def test_render_thinking_helper_returns_panel(self) -> None:
        frame = ThinkingEvent(thread_id="t", delta="x")
        r = render_thinking(frame)
        assert isinstance(r, Panel)

    def test_render_tool_call_helper_returns_panel(self) -> None:
        frame = ToolCallEvent(
            thread_id="t", call_id="c", name="n", arguments={}, local=True
        )
        r = render_tool_call(frame)
        assert isinstance(r, Panel)
