"""When a saved automation is next due.

Modeled on T3 Code's scheduled tasks (pingdotgg/t3code): the clock lives on
the server, a fire is identified by the slot it belongs to, and downtime
collapses into one catch-up instead of a run per missed interval. Fixed
times use the task's timezone.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_CRON = re.compile(r"^\s*(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+(\*|[0-7](?:\s*,\s*[0-7])*)\s*$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


class ScheduleError(ValueError):
    pass


def require_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError(f"Unknown timezone {name}.") from exc


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_schedule(raw: dict | None) -> dict:
    """A schedule the runner can turn into instants. Raises ScheduleError when it cannot."""
    data = dict(raw or {})
    if data.get("cron"):
        data = {**data, **parse_cron(str(data["cron"]))}
    kind = str(data.get("kind") or "").lower()
    if kind not in {"once", "daily", "weekly"}:
        raise ScheduleError("Choose a one-time date, a daily time, or a weekly time.")
    if kind == "once":
        at = str(data.get("at") or "").strip()
        if not at:
            raise ScheduleError("A one-time automation needs a date and time.")
        _parse_wall(at)
        return {"kind": "once", "at": at}
    clock = _clock(data.get("time") or "09:00")
    if kind == "daily":
        return {"kind": "daily", "time": clock}
    days = _weekdays(data.get("weekdays"))
    if not days:
        raise ScheduleError("A weekly automation needs at least one day.")
    return {"kind": "weekly", "time": clock, "weekdays": days}


def parse_cron(expression: str) -> dict:
    """A small cron: ``M H * * *`` daily, or ``M H * * D`` / ``D,D`` weekly.

    The day number follows cron (0 and 7 are Sunday). Anything richer is refused
    so a mistyped expression cannot silently mean something else.
    """
    match = _CRON.match(expression or "")
    if not match:
        raise ScheduleError("Use a cron like '0 9 * * *' (daily) or '0 9 * * 1' (weekly).")
    minute, hour, day = int(match.group(1)), int(match.group(2)), match.group(3)
    if minute > 59 or hour > 23:
        raise ScheduleError("That cron time is not a real time of day.")
    clock = f"{hour:02d}:{minute:02d}"
    if day == "*":
        return {"kind": "daily", "time": clock}
    weekdays = []
    for part in day.split(","):
        number = int(part.strip())
        if number in {0, 7}:
            weekdays.append(6)
        elif 1 <= number <= 6:
            weekdays.append(number - 1)
        else:
            raise ScheduleError("Cron weekdays are 0–7, with 0 and 7 meaning Sunday.")
    return {"kind": "weekly", "time": clock, "weekdays": sorted(set(weekdays))}


def first_due(schedule: dict, timezone: str, now: datetime) -> datetime | None:
    """The first slot to run. A one-time time already in the past is still returned, so it catch up once."""
    now = as_utc(now)
    if schedule["kind"] == "once":
        return _once(schedule, timezone)
    slot = next_after(schedule, timezone, now - timedelta(microseconds=1))
    return slot


def next_after(schedule: dict, timezone: str, after: datetime) -> datetime | None:
    """The next slot strictly after ``after``. A one-time schedule has none once its time has passed."""
    after = as_utc(after)
    if schedule["kind"] == "once":
        when = _once(schedule, timezone)
        return when if when > after else None
    zone = require_timezone(timezone)
    local_after = after.astimezone(zone)
    hour, minute = _split_clock(schedule["time"])
    days = set(schedule.get("weekdays") or [])
    for offset in range(0, 8):
        day = local_after.date() + timedelta(days=offset)
        if schedule["kind"] == "weekly" and day.weekday() not in days:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        if candidate > local_after:
            return candidate.astimezone(UTC)
    return None


def advance_after_run(schedule: dict, timezone: str, scheduled_for: datetime, now: datetime) -> datetime | None:
    """One catch-up, then the next slot that is still in the future.

    Several missed mornings become a single run. The following due time is the
    next one after now, not a queue of the mornings in between.
    """
    if schedule["kind"] == "once":
        return None
    cursor = as_utc(scheduled_for)
    nxt = next_after(schedule, timezone, cursor)
    now = as_utc(now)
    while nxt is not None and nxt <= now:
        nxt = next_after(schedule, timezone, nxt)
    return nxt


def describe(schedule: dict, timezone: str) -> str:
    kind = schedule.get("kind")
    if kind == "once":
        return f"Once at {schedule.get('at')} ({timezone})"
    clock = schedule.get("time") or ""
    if kind == "daily":
        return f"Daily at {clock} ({timezone})"
    names = [WEEKDAYS[day].capitalize() for day in schedule.get("weekdays") or [] if 0 <= day <= 6]
    return f"Weekly on {', '.join(names) or 'no days'} at {clock} ({timezone})"


def _once(schedule: dict, timezone: str) -> datetime:
    wall = _parse_wall(schedule["at"])
    if wall.tzinfo is not None:
        return wall.astimezone(UTC)
    return wall.replace(tzinfo=require_timezone(timezone)).astimezone(UTC)


def _parse_wall(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ScheduleError("Use a date and time like 2026-11-03T09:00.") from exc
    return parsed


def _clock(value: str) -> str:
    match = _TIME.match(str(value).strip())
    if not match:
        raise ScheduleError("Use a time like 09:00.")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ScheduleError("That time is not a real time of day.")
    return f"{hour:02d}:{minute:02d}"


def _split_clock(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _weekdays(value) -> list[int]:
    if not value:
        return []
    days = []
    for item in value:
        if isinstance(item, str) and item.lower() in WEEKDAYS:
            days.append(WEEKDAYS.index(item.lower()))
            continue
        try:
            number = int(item)
        except (TypeError, ValueError) as exc:
            raise ScheduleError("Weekdays are Monday (0) through Sunday (6).") from exc
        if number < 0 or number > 6:
            raise ScheduleError("Weekdays are Monday (0) through Sunday (6).")
        days.append(number)
    return sorted(set(days))
