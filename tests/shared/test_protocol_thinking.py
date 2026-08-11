"""Serde + discrimination tests for the ThinkingEvent wire frame."""

from __future__ import annotations

from shared.protocol import (
    CORE_TAGS,
    ThinkingEvent,
    load_core,
)


def test_thinking_event_serde_roundtrip() -> None:
    frame = ThinkingEvent(thread_id="t1", delta="reasoning...")
    text = frame.model_dump_json()
    back = load_core(text)
    assert isinstance(back, ThinkingEvent)
    assert back.thread_id == "t1"
    assert back.delta == "reasoning..."
    assert back.type == "thinking"


def test_thinking_event_in_core_tags() -> None:
    assert "thinking" in CORE_TAGS


def test_load_core_discriminates_thinking() -> None:
    frame = ThinkingEvent(thread_id="t2", delta="step")
    parsed = load_core(frame.model_dump_json())
    assert isinstance(parsed, ThinkingEvent)
    assert parsed.delta == "step"


def test_thinking_event_schema_shape() -> None:
    # Bare model_dump to confirm the wire shape: type + thread_id + delta only.
    frame = ThinkingEvent(thread_id="t3", delta="d")
    dumped = frame.model_dump()
    assert dumped == {"type": "thinking", "thread_id": "t3", "delta": "d"}
