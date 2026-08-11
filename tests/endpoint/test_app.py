"""Tests for :class:`endpoint.ui.app.LocusApp` — frame routing + per-turn state.

Coverage split:

* **State machines** (``TestFrameRouting``): build an app with a no-op fake
  client, call ``_handle_frame`` directly to feed scripted frames, and assert
  the per-turn reactives (thread id, token meter, tool spinner, latency,
  status) update correctly. This avoids the Textual message pump entirely and
  is deterministic and fast.

* **Loop driving a real async generator** (``TestDrainFrames``): the fake
  client's ``events()`` is a real async generator; we mount the app under
  ``run_test()`` and assert a streamed token+final sequence renders into the
  transcript and updates reactives.

* **Slash input** (``TestSlashInput``): drive the real Textual ``Input`` widget
  under ``run_test()`` and assert ``/new`` clears the thread, ``/clear`` wipes
  the transcript, and a plain message calls ``send_user_message`` with the
  pinned thread id.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from endpoint.settings import CoreSettings, EndpointSettings, UISettings
from endpoint.ui.app import LocusApp
from shared.protocol import (
    ErrorEvent,
    FinalEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)

# --------------------------------------------------------------------------- #
# Fake client
# --------------------------------------------------------------------------- #


class FakeClient:
    """A scripted stand-in for :class:`EndpointClient`.

    ``events()`` is a real async generator over a captured list of frames so
    the app's ``_drain_frames`` loop can consume it. ``send_user_message``
    records its calls so tests can assert routing. ``connect``/``close`` are
    counters; ``connect`` can raise to test error-path rendering.
    """

    def __init__(self, frames: list[Any] | None = None) -> None:
        self.frames = list(frames or [])
        self.sends: list[dict[str, Any]] = []
        self.connect_calls = 0
        self.close_calls = 0
        self.connect_raises: Exception | None = None

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_raises is not None:
            raise self.connect_raises

    async def close(self) -> None:
        self.close_calls += 1

    async def send_user_message(
        self,
        content: str,
        *,
        thread_id: str | None = None,
        provider: str = "auto",
    ) -> None:
        self.sends.append(
            {"content": content, "thread_id": thread_id, "provider": provider}
        )

    async def events(self) -> AsyncIterator[Any]:
        for frame in self.frames:
            yield frame


def _settings() -> EndpointSettings:
    return EndpointSettings(
        core=CoreSettings(url="ws://test.invalid/link"),
        ui=UISettings(),
        link_token="t",
    )


def _app(frames: list[Any] | None = None) -> tuple[LocusApp, FakeClient]:
    client = FakeClient(frames)
    app = LocusApp(client, _settings(), endpoint_id="ep-test")
    return app, client


# --------------------------------------------------------------------------- #
# State machine — _handle_frame directly (no message pump)
# --------------------------------------------------------------------------- #


class TestFrameRouting:
    def test_thread_id_pinned_from_first_frame(self) -> None:
        app, _ = _app()
        app._handle_frame(TokenEvent(thread_id="thr-1", delta="Hi"))
        assert app.thread_id == "thr-1"
        # A later frame with the same id keeps it (no overwrite churn).
        app._handle_frame(TokenEvent(thread_id="thr-1", delta=" there"))
        assert app.thread_id == "thr-1"

    def test_token_meter_counts_chunks(self) -> None:
        app, _ = _app()
        for w in ("Hel", "lo", " world"):
            app._handle_frame(TokenEvent(thread_id="t", delta=w))
        assert app.token_count == 3  # one meter tick per token delta

    def test_thinking_does_not_bump_token_meter(self) -> None:
        app, _ = _app()
        app._handle_frame(ThinkingEvent(thread_id="t", delta="reasoning"))
        app._handle_frame(TokenEvent(thread_id="t", delta="a"))
        assert app.token_count == 1  # thinking ignored by the meter

    def test_tool_call_opens_then_result_closes_spinner(self) -> None:
        app, _ = _app()
        app._handle_frame(
            ToolCallEvent(
                thread_id="t", call_id="c1", name="search", arguments={}, local=True
            )
        )
        assert app.open_tool == "search"
        app._handle_frame(
            ToolResultEvent(thread_id="t", call_id="c1", ok=True, output="ok")
        )
        assert app.open_tool is None

    def test_multiple_open_tools_show_most_recent_open(self) -> None:
        app, _ = _app()
        app._handle_frame(
            ToolCallEvent(thread_id="t", call_id="c1", name="a", arguments={}, local=True)
        )
        app._handle_frame(
            ToolCallEvent(thread_id="t", call_id="c2", name="b", arguments={}, local=True)
        )
        # The spinner surfaces the most-recently-opened call (what's happening now).
        assert app.open_tool == "b"
        app._handle_frame(
            ToolResultEvent(thread_id="t", call_id="c2", ok=True, output="ok")
        )
        # c2 closed, c1 still open -> fall back to a.
        assert app.open_tool == "a"

    def test_final_records_latency_and_resets_status(self) -> None:
        app, _ = _app()
        # Pin the turn clock via the first frame.
        app._handle_frame(TokenEvent(thread_id="t", delta="x"))
        app._handle_frame(FinalEvent(thread_id="t", text="done"))
        assert app.latency_ms is not None and app.latency_ms >= 0
        assert app.status == "idle"

    def test_error_sets_status_not_fatal(self) -> None:
        app, _ = _app()
        app._handle_frame(ErrorEvent(thread_id="t", message="boom", fatal=False))
        assert app.status == "error"

    def test_fatal_error_sets_status_fatal(self) -> None:
        app, _ = _app()
        app._handle_frame(ErrorEvent(thread_id="t", message="kaput", fatal=True))
        assert app.status == "fatal"

    def test_reset_turn_meter_keeps_thread_id(self) -> None:
        app, _ = _app()
        app._handle_frame(TokenEvent(thread_id="thr-9", delta="x"))
        assert app.thread_id == "thr-9"
        assert app.token_count == 1
        app._reset_turn_meter()
        # Meter reset; thread continuity preserved.
        assert app.token_count == 0
        assert app.thread_id == "thr-9"

    def test_reset_turn_state_drops_thread(self) -> None:
        app, _ = _app()
        app._handle_frame(TokenEvent(thread_id="thr-9", delta="x"))
        app._reset_turn_state()
        assert app.thread_id is None
        assert app.token_count == 0
        assert app.status == "idle"


# --------------------------------------------------------------------------- #
# Full drain over a real async generator under run_test()
# --------------------------------------------------------------------------- #


class TestDrainFrames:
    async def test_token_then_final_mounts_and_renders(self) -> None:
        frames = [
            TokenEvent(thread_id="t-drain", delta="Hel"),
            TokenEvent(thread_id="t-drain", delta="lo"),
            FinalEvent(thread_id="t-drain", text="Hello"),
        ]
        app, _ = _app(frames)
        async with app.run_test() as pilot:
            # Let the background _drive coroutine connect + drain the stream.
            await pilot.pause()
            await pilot.pause()
        assert app.thread_id == "t-drain"
        assert app.token_count == 2
        assert app.status == "idle"
        assert app.latency_ms is not None and app.latency_ms >= 0

    async def test_connect_failure_renders_error_and_disconnects(self) -> None:
        client = FakeClient([])
        client.connect_raises = RuntimeError("no core")
        app = LocusApp(client, _settings())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
        assert app.status == "disconnected"
        assert client.connect_calls == 1

    async def test_drain_loop_stops_on_fatal(self) -> None:
        frames = [
            TokenEvent(thread_id="t", delta="x"),
            ErrorEvent(thread_id="t", message="die", fatal=True),
            # This would bump the meter if the loop kept going; it must not.
            TokenEvent(thread_id="t", delta="should-not-stream"),
        ]
        app, client = _app(frames)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            await app.shutdown()
        assert app.status == "fatal"
        # Only the first token was processed before the fatal error broke out.
        assert app.token_count == 1
        assert client.close_calls == 1


# --------------------------------------------------------------------------- #
# Slash input driving the real Input widget under run_test()
# --------------------------------------------------------------------------- #


class TestSlashInput:
    async def test_plain_message_routes_to_send_user_message(self) -> None:
        app, client = _app([])
        async with app.run_test() as pilot:
            inp = app.query_one("#prompt")
            inp.value = "hello world"
            await pilot.press("enter")
            await pilot.pause()
        assert len(client.sends) == 1
        assert client.sends[0]["content"] == "hello world"
        assert client.sends[0]["thread_id"] is None  # no thread pinned yet
        assert app.status == "working"

    async def test_new_clears_thread_id(self) -> None:
        app, client = _app([])
        async with app.run_test() as pilot:
            # Pin a thread via an inbound frame first.
            app._handle_frame(TokenEvent(thread_id="thr-1", delta="x"))
            await pilot.pause()
            assert app.thread_id == "thr-1"
            inp = app.query_one("#prompt")
            inp.value = "/new"
            await pilot.press("enter")
            await pilot.pause()
        assert app.thread_id is None
        # /new must NOT send a user message.
        assert client.sends == []

    async def test_clear_wipes_transcript(self) -> None:
        app, _ = _app([])
        cleared: list[bool] = []
        async with app.run_test() as pilot:
            log = app.query_one("#transcript")
            # Render something into the transcript first.
            app._handle_frame(TokenEvent(thread_id="t", delta="hello"))
            await pilot.pause()
            # Spy on the widget's clear(): /clear must route to it. We wrap the
            # bound method so the assert is independent of RichLog internals.
            original_clear = log.clear

            def spy_clear() -> None:
                cleared.append(True)
                original_clear()

            log.clear = spy_clear  # type: ignore[method-assign]
            inp = app.query_one("#prompt")
            inp.value = "/clear"
            await pilot.press("enter")
            await pilot.pause()
        assert cleared == [True]

    async def test_exit_requests_stop(self) -> None:
        app, _ = _app([])
        async with app.run_test() as pilot:
            inp = app.query_one("#prompt")
            inp.value = "/exit"
            await pilot.press("enter")
            await pilot.pause()
        assert app._stop.is_set()

    async def test_continuation_send_pinned_thread_id(self) -> None:
        app, client = _app([])
        async with app.run_test() as pilot:
            app._handle_frame(TokenEvent(thread_id="thr-7", delta="x"))
            await pilot.pause()
            inp = app.query_one("#prompt")
            inp.value = "follow up"
            await pilot.press("enter")
            await pilot.pause()
        assert client.sends[-1]["thread_id"] == "thr-7"
