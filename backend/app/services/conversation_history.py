"""Bounded completed conversation turns for the model, without shared state."""
from __future__ import annotations

from collections.abc import Iterable, Mapping

MAX_HISTORY_TURNS = 8
MAX_HISTORY_CHARS = 16_000


def recent_conversation(messages: Iterable[Mapping[str, str]] | None) -> list[dict[str, str]]:
    """Keep a recent suffix of complete user/assistant turns, never half a turn.

    Historical system/tool roles and reasoning are not model instructions. If
    the newest complete turn alone exceeds the budget, do not substitute an
    older turn that could give "the previous one" a different meaning.
    """
    turns: list[list[dict[str, str]]] = []
    pending: dict[str, str] | None = None
    for message in messages or ():
        role, content = message.get("role"), message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        entry = {"role": role, "content": content}
        if role == "user":
            pending = entry
        elif pending is not None:
            turns.append([pending, entry])
            pending = None
    selected: list[list[dict[str, str]]] = []
    chars = 0
    for turn in reversed(turns[-MAX_HISTORY_TURNS:]):
        size = sum(len(message["content"]) for message in turn)
        if chars + size > MAX_HISTORY_CHARS:
            break
        selected.append(turn)
        chars += size
    return [message for turn in reversed(selected) for message in turn]
