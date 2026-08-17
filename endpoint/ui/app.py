"""Textual TUI for the endpoint: a live transcript + status bar over an ``EndpointClient``.

This is the compositor layer over :mod:`endpoint.ui.tui` (the pure presenter):
the presenter turns parsed frames into rich renderables; this module owns the
Textual ``App``, the layout (header / transcript / input), the background task
that drains ``client.events()``, and the per-turn display state (thread id,
token meter, latency, tool spinner).

Design notes:

* **Client is injected.** ``LocusApp`` takes any object with ``connect()``,
  ``close()``, ``send_user_message(content, *, thread_id, provider)`` and an
  async-generator ``events()`` -- i.e. :class:`EndpointClient` or a test fake.
  The app never imports the concrete client, so ``app.run_test()`` can drive a
  scripted fake without sockets.

* **Slash commands** route through :func:`endpoint.ui.tui.parse_slash`; the app
  handles ``new`` (forget the thread), ``exit`` (quit), ``clear`` (wipe the
  transcript), ``help`` (print a short banner), and ``none`` (send the line as
  a user message). Unknown slash tokens fall through to ``none`` so the model
  can respond to them as plain text.

* **Per-turn state** resets on each ``/new`` and is pinned from the first
  frame's ``thread_id``: the token meter counts streamed token deltas, the
  latency is the wall-time from the first frame to the ``final`` frame, and the
  "tool" field shows the open tool call's name (or ``--``) so the user can see
  what the agent is doing while a tool runs.

* **Reconnect is the caller's job**, same as the REPL: ``run_tui`` connects
  once and tears down on exit. A fatal ``ErrorEvent`` ends the frame loop and
  the app exits after rendering the error panel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, RichLog, Static

from endpoint.ui.tui import parse_slash, render_frame
from protocol import (
    ErrorEvent,
    FinalEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from endpoint.settings import EndpointSettings

__all__ = ["LocusApp", "run_tui"]

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Client protocol (duck-typed EndpointClient for injection / tests)
# --------------------------------------------------------------------------- #


@runtime_checkable
class _Client(Protocol):
    """The slice of :class:`EndpointClient` the app actually calls.

    Declared so ``isinstance`` checks and type hints work for injected fakes
    without depending on ``endpoint.endpoint_conn.client`` here.
    """

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def send_user_message(
        self,
        content: str,
        *,
        thread_id: str | None = None,
        provider: str = "auto",
    ) -> None: ...

    def events(self) -> AsyncIterator[Any]: ...


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #


class LocusApp(App):  # type: ignore[type-arg]
    """A Textual app that renders a core<->endpoint session as a live TUI."""

    BINDINGS: ClassVar[list] = [
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
    ]

    # Per-turn display state. Textual reactives so the status bar auto-refreshes
    # when they change. They reset on every /new and every turn boundary.
    thread_id: reactive[str | None] = reactive(None)
    provider: reactive[str] = reactive("auto")
    token_count: reactive[int] = reactive(0)
    latency_ms: reactive[int | None] = reactive(None)
    open_tool: reactive[str | None] = reactive(None)
    status: reactive[str] = reactive("connecting")

    def __init__(
        self,
        client: _Client,
        settings: EndpointSettings,
        *,
        endpoint_id: str | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._settings = settings
        self._endpoint_id = endpoint_id or "endpoint-1"
        self._turn_start: float | None = None
        # Ordered call_id -> tool name of tool calls awaiting their result.
        # Ordered so the spinner can surface the most-recently-opened call
        # (what's happening now) and fall back to the previous one on close.
        self._open_tools: dict[str, str] = {}
        # An event signaled when the frame loop should stop (quit requested or
        # a fatal error). The background task waits on either.
        self._stop = asyncio.Event()
        self._connect_task: asyncio.Task[None] | None = None
        self._frame_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static("", id="status")
            yield RichLog(id="transcript", markup=False, wrap=True, auto_scroll=True)
            yield Input(id="prompt", placeholder="message or /help; /exit to quit")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Locus endpoint — {self._endpoint_id}"
        self.sub_title = self._settings.core.url
        self._refresh_status()
        # Kick off the connection + frame loop in the background so the UI is
        # interactive immediately. The input box is focused after mount.
        self._connect_task = asyncio.create_task(self._drive())
        self.query_one("#prompt", Input).focus()

    def on_unmount(self) -> None:
        self._stop.set()
        # Cancel any in-flight background work; the client is closed by
        # run_tui's finally block, not here, to keep teardown ownership clear.
        for task in (self._connect_task, self._frame_task):
            if task is not None and not task.done():
                task.cancel()

    # ------------------------------------------------------------------ #
    # Status bar
    # ------------------------------------------------------------------ #

    def watch_thread_id(self, _value: str | None) -> None:
        self._refresh_status()

    def watch_provider(self, _value: str) -> None:
        self._refresh_status()

    def watch_token_count(self, _value: int) -> None:
        self._refresh_status()

    def watch_latency_ms(self, _value: int | None) -> None:
        self._refresh_status()

    def watch_open_tool(self, _value: str | None) -> None:
        self._refresh_status()

    def watch_status(self, _value: str) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        """Re-render the one-line status String above the transcript."""
        thread = self.thread_id or "—"
        latency = "—" if self.latency_ms is None else f"{self.latency_ms}ms"
        tool = "—" if self.open_tool is None else self.open_tool
        line = (
            f" {self._endpoint_id}  |  core {self._settings.core.url}  |  "
            f"thread {thread}  |  provider {self.provider}  |  "
            f"tokens {self.token_count}  |  {latency}  |  tool {tool}  |  "
            f"{self.status}"
        )
        with contextlib.suppress(Exception):  # pre-mount: widget not ready yet
            self.query_one("#status", Static).update(Text(line, style="bold reverse"))

    # ------------------------------------------------------------------ #
    # Connection + frame loop
    # ------------------------------------------------------------------ #

    async def _drive(self) -> None:
        """Connect, then drain ``client.events()`` until stop/fatal/quit."""
        self.status = "connecting"
        try:
            await self._client.connect()
        except Exception as e:
            self._write_error_line(f"could not connect to core: {e}")
            self.status = "disconnected"
            self._stop.set()
            return
        self.status = "idle"
        self._frame_task = asyncio.create_task(self._drain_frames())
        with contextlib.suppress(asyncio.CancelledError):
            await self._frame_task

    async def _drain_frames(self) -> None:
        """Iterate inbound frames, render each, track per-turn state."""
        async for frame in self._client.events():
            if self._stop.is_set():
                break
            self._handle_frame(frame)
            if isinstance(frame, ErrorEvent) and frame.fatal:
                self.status = "fatal"
                self._stop.set()
                break
        if self._open_tools:
            # Link dropped mid-tool; clear the spinner so stale state isn't shown.
            self._open_tools.clear()
            self.open_tool = None

    def _handle_frame(self, frame: Any) -> None:
        """Apply per-turn state updates for one frame, then render it."""
        # Pin the thread id + turn clock from the first frame of the turn.
        tid = getattr(frame, "thread_id", None)
        if tid and self.thread_id is None:
            self.thread_id = tid
            self._turn_start = time.monotonic()

        if isinstance(frame, TokenEvent):
            self.token_count += 1
        elif isinstance(frame, ToolCallEvent):
            self._open_tools[frame.call_id] = frame.name
            self.open_tool = frame.name
        elif isinstance(frame, ToolResultEvent):
            self._open_tools.pop(frame.call_id, None)
            # Surface the most-recently-still-open call's name, else clear.
            names = list(reversed(self._open_tools.values()))
            self.open_tool = names[0] if names else None
        elif isinstance(frame, ThinkingEvent):
            # Thinking chunks don't bump the token meter (ephemeral display).
            pass
        elif isinstance(frame, FinalEvent):
            if self._turn_start is not None:
                self.latency_ms = int((time.monotonic() - self._turn_start) * 1000)
            self.status = "idle"
        elif isinstance(frame, ErrorEvent):
            self.status = "fatal" if frame.fatal else "error"

        self._write_renderable(render_frame(frame))

    def _write_renderable(self, renderable: object) -> None:
        """Append a rendered frame to the transcript RichLog."""
        with contextlib.suppress(Exception):  # pragma: no cover - pre-mount
            self.query_one("#transcript", RichLog).write(renderable)

    def _write_error_line(self, msg: str) -> None:
        self._write_renderable(Text(msg, style="bold red"))

    # ------------------------------------------------------------------ #
    # Input handling
    # ------------------------------------------------------------------ #

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dispatch a submitted input line as a slash command or user turn."""
        line = event.value
        # Clear the input immediately for UX; we still hold the text in `line`.
        event.input.value = ""
        action, _extra = parse_slash(line)
        if action == "exit":
            self._stop.set()
            await self.action_quit()
            return
        if action == "new":
            self._reset_turn_state()
            self._write_renderable(Text("— new thread —", style="dim italic"))
            return
        if action == "clear":
            with contextlib.suppress(Exception):  # pragma: no cover
                self.query_one("#transcript", RichLog).clear()
            return
        if action == "help":
            self._write_renderable(
                Text(
                    "commands: /new  /clear  /exit  /help   |   "
                    "type a message to send, Enter to submit",
                    style="dim",
                )
            )
            return
        # action == "none": plain user text -> start a turn.
        text = line.strip()
        if not text:
            return
        self._write_renderable(Text(f"> {text}", style="bold"))
        self.status = "working"
        self._reset_turn_meter()
        try:
            await self._client.send_user_message(
                text,
                thread_id=self.thread_id,
                provider=self.provider,
            )
        except Exception as e:
            self._write_error_line(f"send failed: {e}")
            self.status = "idle"

    def _reset_turn_meter(self) -> None:
        """Reset the per-turn counters at the start of a user turn (not the thread id)."""
        self.token_count = 0
        self.latency_ms = None
        self._open_tools.clear()
        self.open_tool = None
        self._turn_start = None

    def _reset_turn_state(self) -> None:
        """Full reset for /new: forget the thread + per-turn counters."""
        self._reset_turn_meter()
        self.thread_id = None
        self.status = "idle"

    # ------------------------------------------------------------------ #
    # Public lifecycle for run_tui
    # ------------------------------------------------------------------ #

    async def shutdown(self) -> None:
        """Signal the background tasks to stop and close the client."""
        self._stop.set()
        for task in (self._frame_task, self._connect_task):
            if task is not None and not task.done():
                task.cancel()
        await self._client.close()


async def run_tui(
    settings: EndpointSettings,
    *,
    endpoint_id: str | None = None,
    adhoc_tools: dict[str, Any] | None = None,
    provider: str = "auto",
) -> int:
    """Connect to core and run the TUI. Returns a process exit code."""
    from endpoint.endpoint_conn.client import EndpointClient

    client: _Client = EndpointClient(
        settings, endpoint_id=endpoint_id, adhoc_tools=adhoc_tools or {}
    )
    app = LocusApp(client, settings, endpoint_id=endpoint_id)
    app.provider = provider
    try:
        await app.run_async()
        return 0
    except Exception as e:  # pragma: no cover - defensive on app failure
        _log.error("TUI exited with error: %s", e)
        return 1
    finally:
        await client.close()
