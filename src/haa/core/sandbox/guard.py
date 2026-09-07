"""Last-line-of-defense filter for sandbox output before it reaches the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field

from haa.core.tools.profiler import GPS_PAIR_RE, PHONE_RE

REDACTED = "[REDACTED]"
_LINE_HEADROOM = 10


@dataclass
class FilterResult:
    text: str
    notices: list[str] = field(default_factory=list)


def filter_output(text: str, *, row_cap: int, size_cap: int) -> FilterResult:
    notices: list[str] = []

    redactions = 0
    for pattern in (PHONE_RE, GPS_PAIR_RE):
        text, n = pattern.subn(REDACTED, text)
        redactions += n
    if redactions:
        notices.append(f"[guard] redacted {redactions} PII-looking value(s)")

    lines = text.splitlines()
    cap = row_cap + _LINE_HEADROOM
    if len(lines) > cap:
        hidden = len(lines) - cap
        text = "\n".join(lines[:cap])
        notices.append(f"[guard] output truncated: {hidden} more lines hidden")

    raw = text.encode("utf-8")
    if len(raw) > size_cap:
        text = raw[:size_cap].decode("utf-8", errors="ignore")
        notices.append("[guard] output truncated to size limit")

    return FilterResult(text=text, notices=notices)
