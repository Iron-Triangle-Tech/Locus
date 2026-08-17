"""Pure presenter for the endpoint TUI: slash-command parser + frame renderers.

Kept free of any Textual ``App`` so the rendering helpers + slash parser can be
unit-tested without spinning the event loop. :mod:`endpoint.ui.app` composes
these renderables into the running app.

Rendering rules (matching the phase plan):

* ``TokenEvent``      -> plain text appended to the active assistant line.
* ``ThinkingEvent``   -> a muted, bordered block labelled "thinking".
* ``ToolCallEvent``   -> a highlighted bordered block (cyan/blue) with the tool
  name + args; tagged "core" or "endpoint" via ``frame.local``.
* ``ToolResultEvent`` -> a bordered result block, green when ``ok`` else red.
* ``FinalEvent``      -> plain text; the app closes the turn and updates stats.
* ``ErrorEvent``      -> red bordered block; fatal ones also flip status.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from rich.panel import Panel
from rich.text import Text

from protocol import (
    ErrorEvent,
    FinalEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)

__all__ = [
    "SlashAction",
    "parse_slash",
    "render_error",
    "render_frame",
    "render_thinking",
    "render_tool_call",
    "render_tool_result",
]

# Slash commands shipped in v1 (keep tight per phase plan).
SlashAction = Literal["new", "exit", "clear", "help", "none"]
SlashResult = tuple[SlashAction, str | None]
# The second element is the trailing argument/help-text payload, if any.


def parse_slash(line: str) -> SlashResult:
    """Classify a user input line.

    Returns ``(action, extra)``. ``action == "none"`` means the line is plain
    user text (or an unknown command, which we treat as plain text so the agent
    can respond to it rather than silently dropping it).
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return "none", None
    parts = stripped.split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else None
    if cmd == "/new":
        return "new", None
    if cmd == "/exit" or cmd == "/quit":
        return "exit", None
    if cmd == "/clear":
        return "clear", None
    if cmd == "/help":
        return "help", rest
    # Unknown slash command: treat as plain user text. The model can decide.
    return "none", None


# --------------------------------------------------------------------------- #
# Frame renderers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ThinkingText:
    """A plain-text carrier for a thinking delta, tagged for the app to route."""

    delta: str


def render_thinking(frame: ThinkingEvent) -> Panel:
    """Muted, bordered block labelled "thinking". Plain text inside (not markdown)."""
    body = Text(frame.delta, style="dim italic")
    return Panel(body, title="thinking", title_align="left", border_style="dim cyan")


def render_tool_call(frame: ToolCallEvent) -> Panel:
    """Highlighted bordered block; "core" or "endpoint" tag from ``frame.local``."""
    who = "core" if frame.local else "endpoint"
    args = json.dumps(frame.arguments, default=str)
    body = Text.assemble(
        (f"{who}  ", "bold"),
        (frame.name, "bold cyan"),
        (" ", ""),
        (args, "white"),
    )
    return Panel(body, title="tool_call", title_align="left", border_style="cyan")


def render_tool_result(frame: ToolResultEvent) -> Panel:
    """Bordered result block: green when ok else red. Output or error body."""
    if frame.ok:
        body = Text(frame.output or "", style="")
        return Panel(body, title="tool_result ok", title_align="left", border_style="green")
    body = Text(frame.error or "unknown error", style="bold red")
    return Panel(
        body, title="tool_result ERROR", title_align="left", border_style="red"
    )


def render_error(frame: ErrorEvent) -> Panel:
    """Red bordered block; the app also flips the status bar on fatal."""
    title = "FATAL" if frame.fatal else "error"
    body = Text(frame.message, style="bold red")
    return Panel(body, title=title, title_align="left", border_style="red")


def render_frame(frame: object) -> object:
    """Dispatch a parsed ``CoreFrame`` to its rich renderable.

    ``TokenEvent`` returns a ``Text`` (to append to the active assistant line,
    not a panel). ``FinalEvent`` returns a ``Text``. The rest return ``Panel``.
    The app treats the return value as a rich renderable; tests assert against
    stringified content / styles.
    """
    if isinstance(frame, TokenEvent):
        return Text(frame.delta)
    if isinstance(frame, FinalEvent):
        return Text(frame.text)
    if isinstance(frame, ThinkingEvent):
        return render_thinking(frame)
    if isinstance(frame, ToolCallEvent):
        return render_tool_call(frame)
    if isinstance(frame, ToolResultEvent):
        return render_tool_result(frame)
    if isinstance(frame, ErrorEvent):
        return render_error(frame)
    # Defensive: unknown frame type -> a visible placeholder so the user sees
    # something happened rather than a silent drop.
    return Text(f"[unhandled frame: {type(frame).__name__}]")
