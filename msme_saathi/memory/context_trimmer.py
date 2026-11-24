"""Short-term context management: message window trimming & PII-scrubbed turn summarization."""

from __future__ import annotations

from typing import Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage

from msme_saathi.memory.store import scrub_pii_text


def estimate_tokens(text: str) -> int:
    """Rough multilingual token estimator (~3.5 chars/token for mixed Latin + Devanagari)."""
    if not text:
        return 0
    return max(1, int(len(text) / 3.5))


def trim_or_summarize_messages(
    messages: Sequence[AnyMessage],
    *,
    max_recent_messages: int = 6,
    max_tokens: int = 1200,
) -> list[AnyMessage]:
    """Keep system instructions + recent turns within `max_tokens`, summarizing older history safely."""
    if not messages:
        return []

    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    conversational = [m for m in messages if not isinstance(m, SystemMessage)]

    if len(conversational) <= max_recent_messages:
        total = sum(estimate_tokens(str(m.content)) for m in messages)
        if total <= max_tokens:
            return list(messages)

    older = conversational[:-max_recent_messages] if max_recent_messages > 0 else conversational
    recent = conversational[-max_recent_messages:] if max_recent_messages > 0 else []

    # Trim recent further if token ceiling is still exceeded
    while len(recent) > 2:
        tok_count = sum(estimate_tokens(str(m.content)) for m in (*system_msgs, *recent))
        if tok_count <= max_tokens:
            break
        older.append(recent.pop(0))

    summary_bullets: list[str] = []
    for m in older[-8:]:
        role = "User" if isinstance(m, HumanMessage) else ("Assistant" if isinstance(m, AIMessage) else "Note")
        snippet = scrub_pii_text(str(m.content).replace("\n", " ").strip())[:120]
        if snippet:
            summary_bullets.append(f"- {role}: {snippet}")

    summary_msg = SystemMessage(
        content="[Trimmed Short-Term Context Summary]\n" + "\n".join(summary_bullets)
    )
    return [*system_msgs, summary_msg, *recent]
