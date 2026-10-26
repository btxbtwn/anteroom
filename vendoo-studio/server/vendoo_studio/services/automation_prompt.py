"""Turn a seller's sentence into a structured automation.

The assistant and ``anteroom automations create --prompt`` both use this.
Nothing here saves or runs a task. A draft that does not name its
marketplaces, its schedule, or the size of a price change stays unready
until the seller fills those in and confirms.
"""
from __future__ import annotations

import re
from datetime import datetime

from vendoo_studio.services.automation_schedule import WEEKDAYS, ScheduleError, normalize_schedule

MARKETPLACES = ("ebay", "poshmark", "mercari", "depop", "etsy", "sellwild", "vinted")
_MARKETPLACE_WORDS = {
    "ebay": "ebay", "e-bay": "ebay", "poshmark": "poshmark", "mercari": "mercari",
    "depop": "depop", "etsy": "etsy", "sellwild": "sellwild", "vinted": "vinted",
}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_FIELD_NAMES = {
    "title": "title", "description": "description", "brand": "brand", "condition": "condition",
    "size": "size", "sku": "sku", "department": "department", "category": "category_path",
}


def draft_from_prompt(prompt: str, *, timezone: str = "UTC", now: datetime | None = None) -> dict:
    """The structured task a sentence describes, plus ``issues`` when it is not ready to confirm."""
    text = " ".join((prompt or "").split())
    lowered = text.lower()
    if not text:
        return _empty("Write what the automation should do.")
    actions = _actions(lowered, text)
    marketplaces = _marketplaces(lowered)
    filters = _filters(lowered)
    schedule, schedule_issue = _schedule(lowered, timezone, now)
    issues = []
    if schedule_issue:
        issues.append(schedule_issue)
    if not actions:
        issues.append("Say what to do: delist, relist, regenerate, drop the price, or update a field.")
    if any(action["type"] in {"delist", "relist"} for action in actions) and not marketplaces:
        issues.append("Name the marketplaces. An automation does not run on every marketplace by default.")
    if any(action["type"] == "price" and action.get("value") in (None, 0) for action in actions):
        issues.append("Say how much to drop the price, as $5 or 10%.")
    if re.search(r"\ball marketplaces\b|\bevery marketplace\b", lowered):
        issues.append("Name each marketplace. \"All marketplaces\" is not a confirmation.")
    if "sold" in filters.get("status", ""):
        issues.append("Sold items are never changed.")
    name = _name(text, filters, actions)
    spec = {
        "op": "create",
        "name": name,
        "prompt": text,
        "timezone": timezone or "UTC",
        "schedule": schedule,
        "filters": filters,
        "actions": actions,
        "marketplaces": marketplaces,
        "issues": issues,
        "ready": not issues and schedule is not None,
    }
    return spec


def _empty(issue: str) -> dict:
    return {
        "op": "create", "name": "", "prompt": "", "timezone": "UTC", "schedule": None,
        "filters": {}, "actions": [], "marketplaces": [], "issues": [issue], "ready": False,
    }


def _actions(lowered: str, original: str) -> list[dict]:
    found: dict[str, dict] = {}
    if re.search(r"\bregenerate\b|\brewrite the listing", lowered):
        found["regenerate"] = {"type": "regenerate"}
    price = _price(lowered)
    if price:
        found["price"] = price
    for match in re.finditer(
        r"\bset (title|description|brand|condition|size|sku|department|category) to [\"“](.+?)[\"”]",
        original, re.IGNORECASE,
    ):
        found.setdefault("update_fields", {"type": "update_fields", "fields": {}})
        found["update_fields"]["fields"][_FIELD_NAMES[match.group(1).lower()]] = match.group(2).strip()
    if re.search(r"\bdelist\b", lowered):
        found["delist"] = {"type": "delist"}
    if re.search(r"\brelist\b|\blist them again\b|\blist it again\b", lowered):
        found["relist"] = {"type": "relist"}
        # Relist already takes the listing down, so a separate delist would run twice.
        found.pop("delist", None)
    order = ("regenerate", "update_fields", "price", "delist", "relist")
    return [found[name] for name in order if name in found]


def _price(lowered: str) -> dict | None:
    mentions_drop = re.search(r"\b(drop|lower|reduce|mark down|markdown|discount)\b", lowered)
    if not mentions_drop:
        return None
    if "price" not in lowered and "$" not in lowered and "%" not in lowered:
        return None
    percent = re.search(r"(\d+(?:\.\d+)?)\s*%", lowered)
    # A dollar figure after "below" / "floor" is the floor, not the drop.
    drop_amount = None
    for match in re.finditer(r"\$\s*(\d+(?:\.\d+)?)", lowered):
        window = lowered[max(0, match.start() - 28):match.start()]
        if re.search(r"below|floor|minimum|no lower|not under", window):
            continue
        drop_amount = float(match.group(1))
        break
    action: dict = {"type": "price"}
    # Prefer an explicit percent when the sentence is a percent off, even if a floor is in dollars.
    floor_span = re.search(r"(?:don't go below|do not go below|not below|no lower than).*$", lowered)
    percent_is_floor = bool(floor_span and percent and percent.start() >= floor_span.start())
    if percent and not percent_is_floor:
        action["mode"] = "percent"
        action["value"] = float(percent.group(1))
    elif drop_amount is not None:
        action["mode"] = "amount"
        action["value"] = drop_amount
    else:
        action["mode"] = "amount"
        action["value"] = None
    floor = re.search(
        r"(?:don't go below|do not go below|not below|no lower than|minimum(?: of)?|floor(?: of)?)\s*\$\s*(\d+(?:\.\d+)?)",
        lowered,
    )
    if floor:
        action["floor"] = float(floor.group(1))
    floor_percent = re.search(
        r"(?:don't go below|do not go below|not below|no lower than)\s*(\d+(?:\.\d+)?)\s*%",
        lowered,
    )
    if floor_percent:
        action["floor_percent"] = float(floor_percent.group(1))
    return action


def _marketplaces(lowered: str) -> list[str]:
    found = []
    for word, name in _MARKETPLACE_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered) and name not in found:
            found.append(name)
    return found


def _filters(lowered: str) -> dict:
    filters: dict = {}
    if re.search(r"\bmen's\b|\bmens\b|\bmen\b", lowered) and not re.search(r"\bwomen's\b|\bwomens\b|\bwomen\b", lowered):
        filters["department"] = "men"
    elif re.search(r"\bwomen's\b|\bwomens\b|\bwomen\b", lowered):
        filters["department"] = "women"
    if re.search(r"\bt-?\s*shirts?\b|\btees\b|\btee shirts?\b", lowered):
        filters["category"] = "t-shirt"
    brand = re.search(r"\bbrand\s+([a-z0-9][a-z0-9'&.-]*)", lowered)
    if brand and brand.group(1) not in {"the", "a", "new"}:
        filters["brand"] = brand.group(1)
    age = re.search(r"(\d+)\s*days", lowered)
    if age and re.search(r"reach|older|listed|sitting|after|age", lowered):
        filters["min_age_days"] = int(age.group(1))
    status = re.search(r"\bstatus\s+(active|draft|sold|listing)\b", lowered)
    if status:
        filters["status"] = status.group(1)
    # An id is our 12-character hex, or a token with a digit. Plain words ("item reaches") are not ids.
    ids = re.findall(r"\b(?:item|listing)\s+(?:id\s+)?([a-z0-9]{8,16})\b", lowered)
    ids = [item for item in ids if any(char.isdigit() for char in item) or re.fullmatch(r"[a-f0-9]{12}", item)]
    if ids:
        filters["conversation_ids"] = ids
    return filters


def _schedule(lowered: str, timezone: str, now: datetime | None) -> tuple[dict | None, str | None]:
    clock = _clock(lowered) or "09:00"
    weekly = _weekly_days(lowered)
    once = _once_at(lowered, clock, now)
    daily = bool(re.search(r"\bevery day\b|\bdaily\b|\beach day\b", lowered))
    age = bool(re.search(r"\d+\s*days", lowered) and re.search(r"reach|older|listed|sitting|after|age", lowered))
    try:
        if once and not daily and not weekly:
            return normalize_schedule({"kind": "once", "at": once}), None
        if weekly:
            return normalize_schedule({"kind": "weekly", "time": clock, "weekdays": weekly}), None
        if daily or age:
            # An age rule is checked every day at the stated time (09:00 when none was given).
            return normalize_schedule({"kind": "daily", "time": clock}), None
    except ScheduleError as exc:
        return None, str(exc)
    if not once and not daily and not weekly and not age:
        return None, "Say when it runs: a date and time, every day, every Monday, or when an item reaches a number of days."
    return None, "The schedule could not be read."


def _clock(lowered: str) -> str | None:
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered)
    if not match:
        return None
    hour, minute, suffix = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _weekly_days(lowered: str) -> list[int]:
    if not re.search(r"\bevery\b|\bweekly\b|\beach\b", lowered):
        return []
    days = [index for index, name in enumerate(WEEKDAYS) if re.search(rf"\b{name}\b", lowered)]
    return days


def _once_at(lowered: str, clock: str, now: datetime | None) -> str | None:
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})(?:[t ](\d{2}:\d{2}))?\b", lowered)
    if iso:
        return f"{iso.group(1)}T{iso.group(2) or clock}"
    named = re.search(
        r"\bon\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?",
        lowered,
    )
    if not named:
        return None
    year = int(named.group(3) or (now.year if now else datetime.now().year))
    month = _MONTHS[named.group(1)]
    day = int(named.group(2))
    return f"{year:04d}-{month:02d}-{day:02d}T{clock}"


def _name(text: str, filters: dict, actions: list[dict]) -> str:
    if filters.get("min_age_days") and any(action["type"] == "relist" for action in actions):
        return f"Relist after {filters['min_age_days']} days"
    trimmed = text.strip()
    if len(trimmed) <= 80:
        return trimmed[:1].upper() + trimmed[1:] if trimmed else "Automation"
    return trimmed[:77].rstrip() + "..."
