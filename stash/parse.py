"""Deterministic note parsing for stash.

No generative AI. The raw note is preserved verbatim and never altered.
Structure (tags, category, intent, reminder time) is extracted with plain
code so the system can never insert content the user didn't write.
"""

import re
from dataclasses import dataclass, field

_TAG_RE = re.compile(r"#(\w+)")
_CATEGORY_RE = re.compile(r"@(\w+)")

# "in 30min", "in 2 hours", "in 1 day" -> (amount, unit)
_DURATION_RE = re.compile(
    r"\bin\s+(\d+)\s*"
    r"(s|sec|secs|second|seconds"
    r"|m|min|mins|minute|minutes"
    r"|h|hr|hrs|hour|hours"
    r"|d|day|days)\b",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}


def _parse_reminder(text: str) -> tuple[str, int | None]:
    """Return (intent, remind_in_seconds). Deterministic, no LLM."""
    if "remind" not in text.lower():
        return "note", None
    match = _DURATION_RE.search(text)
    if not match:
        return "reminder", None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return "reminder", amount * _UNIT_SECONDS[unit]


@dataclass
class ParsedNote:
    raw: str
    tags: list[str] = field(default_factory=list)
    category: str | None = None
    intent: str = "note"
    remind_in_seconds: int | None = None


def parse(text: str) -> ParsedNote:
    category_match = _CATEGORY_RE.search(text)
    intent, remind_in_seconds = _parse_reminder(text)
    return ParsedNote(
        raw=text,
        tags=_TAG_RE.findall(text),
        category=category_match.group(1) if category_match else None,
        intent=intent,
        remind_in_seconds=remind_in_seconds,
    )
