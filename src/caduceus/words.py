"""Times and spans in the words the screens use: "1:12 pm", "today at 1:12 pm", "25 minutes".

Apart from the presenter, so the workflow can phrase a refusal without importing the screens.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo


def clock(dt: datetime, tz: tzinfo) -> str:
    t = dt.astimezone(tz)
    if t.hour == 12 and t.minute == 0:
        return "noon"
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def moment(dt: datetime | str, now: datetime, tz: tzinfo) -> str:
    """'today at 1:12 pm', or 'October 28, 2026 at 9:05 am' for another day."""
    dt = datetime.fromisoformat(dt) if isinstance(dt, str) else dt
    day = dt.astimezone(tz).date()
    return f"{'today' if day == now.astimezone(tz).date() else long_date(day)} at {clock(dt, tz)}"


def took(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "under a minute"
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    return f"{hours} hours" if hours < 48 else f"{hours // 24} days"
