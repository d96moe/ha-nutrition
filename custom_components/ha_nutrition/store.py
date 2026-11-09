"""The food log: every entry and day note, in one Home Assistant Store file.

The key is fixed (not per config entry) and the integration never deletes it:
once the markdown log is retired this file is the only copy, and "remove and
re-add the integration" must not lose it. Writes are serialised with a lock and
saved at once (atomic file write); listeners then get the changed days.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .models import FoodEntry, InvalidInput, day_totals, parse_date, validate_fields

STORE_KEY = "ha_nutrition.log"
STORE_VERSION = 1
STORE_MINOR_VERSION = 1
REQUEST_ID_DAYS = 7
MAX_ENTRIES_PER_CALL = 50
EXPORT_FORMAT = "ha_nutrition.log"

Listener = Callable[[set[date]], None]


class UnknownEntry(InvalidInput):
    """No entry with that id."""


def _sort_key(e: FoodEntry) -> tuple[time, datetime]:
    return (e.time or time.max, e.created_at)


class NutritionStore:
    def __init__(self, hass: HomeAssistant, key: str = STORE_KEY) -> None:
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, key, minor_version=STORE_MINOR_VERSION
        )
        self._lock = asyncio.Lock()
        self._entries: dict[str, FoodEntry] = {}
        self._notes: dict[date, str] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._listeners: list[Listener] = []

    # ------------------------------------------------------------------ read
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._entries = {k: FoodEntry.from_dict(v) for k, v in data.get("entries", {}).items()}
        self._notes = {date.fromisoformat(k): v for k, v in data.get("notes", {}).items()}
        self._requests = dict(data.get("requests", {}))

    def all_entries(self) -> list[FoodEntry]:
        return sorted(self._entries.values(), key=lambda e: (e.date, *_sort_key(e)))

    def entries_on(self, day: date) -> list[FoodEntry]:
        return sorted((e for e in self._entries.values() if e.date == day), key=_sort_key)

    def entries_between(self, start: date, end: date) -> list[FoodEntry]:
        return [e for e in self.all_entries() if start <= e.date <= end]

    def days_with_entries(self) -> set[date]:
        return {e.date for e in self._entries.values()}

    def get(self, entry_id: str) -> FoodEntry:
        try:
            return self._entries[entry_id]
        except KeyError:
            raise UnknownEntry(f"no entry with id {entry_id!r}; get_nutrition with include_entries lists ids") from None

    def note(self, day: date) -> str | None:
        return self._notes.get(day)

    def notes_between(self, start: date, end: date) -> dict[date, str]:
        return {d: n for d, n in sorted(self._notes.items()) if start <= d <= end}

    def totals(self, day: date) -> dict[str, Any]:
        return day_totals(day, self.entries_on(day)).to_dict()

    def export(self) -> dict[str, Any]:
        return {
            "format": EXPORT_FORMAT,
            "version": STORE_VERSION,
            "entries": [e.to_dict() for e in self.all_entries()],
            "notes": {d.isoformat(): n for d, n in sorted(self._notes.items())},
        }

    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # ------------------------------------------------------------------ write
    @staticmethod
    def _today() -> date:
        return dt_util.now().date()

    async def _async_commit(self, changed: set[date]) -> None:
        cutoff = dt_util.utcnow() - timedelta(days=REQUEST_ID_DAYS)
        self._requests = {
            k: v for k, v in self._requests.items() if datetime.fromisoformat(v["at"]) >= cutoff
        }
        await self._store.async_save({
            "entries": {k: e.to_dict() for k, e in self._entries.items()},
            "notes": {d.isoformat(): n for d, n in self._notes.items()},
            "requests": self._requests,
        })
        for listener in list(self._listeners):
            listener(set(changed))

    async def async_log_entries(
        self, day: date | None, items: list[dict[str, Any]], *, source: str, request_id: str | None = None
    ) -> dict[str, Any]:
        today = self._today()
        day = day or today
        if day > today:
            raise InvalidInput(f"date {day.isoformat()} is in the future (today is {today.isoformat()})")
        if not 1 <= len(items) <= MAX_ENTRIES_PER_CALL:
            raise InvalidInput(f"entries must hold 1 to {MAX_ENTRIES_PER_CALL} items, got {len(items)}")
        async with self._lock:
            if request_id and (earlier := self._requests.get(request_id)):
                if earlier["result"]["date"] != day.isoformat():
                    # A reused id, not a retry: refusing beats silently dropping a meal.
                    raise InvalidInput(
                        f"request_id {request_id!r} was already used for {earlier['result']['date']}; "
                        "use a new, unique id for each user message"
                    )
                return {**earlier["result"], "repeated": True}
            fields_list = []
            for number, item in enumerate(items, 1):
                if "date" in item:
                    raise InvalidInput("set date once for the call, not per entry")
                try:
                    fields_list.append(validate_fields(item, partial=False, today=today))
                except InvalidInput as exc:
                    raise InvalidInput(f"entry {number}: {exc}") from None
            existing = self.entries_on(day)
            now = dt_util.utcnow()
            added = []
            for fields in fields_list:
                entry = FoodEntry(
                    id=ulid_now(), date=day, category=fields["category"], description=fields["description"],
                    kcal=fields["kcal"], time=fields.get("time"), protein_g=fields.get("protein_g"),
                    sat_fat_g=fields.get("sat_fat_g"), alcohol_units=fields.get("alcohol_units"),
                    source=source, created_at=now, updated_at=now,
                )
                self._entries[entry.id] = entry
                added.append(entry)
            duplicates = [
                old for old in existing
                if any(_same_item(old, new) for new in added)
            ]
            result = {
                "date": day.isoformat(),
                "entries": [e.to_dict() for e in added],
                "totals": self.totals(day),
                "possible_duplicates": [e.to_dict() for e in duplicates],
                "repeated": False,
            }
            if request_id:
                self._requests[request_id] = {"at": now.isoformat(), "result": result}
            await self._async_commit({day})
            return result

    async def async_update_entry(self, entry_id: str, fields: dict[str, Any], *, source: str) -> dict[str, Any]:
        async with self._lock:
            entry = self.get(entry_id)
            changes = validate_fields(fields, partial=True, today=self._today())
            old_day = entry.date
            for name, value in changes.items():
                setattr(entry, name, value)
            entry.source = source
            entry.updated_at = dt_util.utcnow()
            await self._async_commit({old_day, entry.date})
            return {"entry": entry.to_dict(), "totals": self.totals(entry.date)}

    async def async_delete_entry(self, entry_id: str) -> dict[str, Any]:
        async with self._lock:
            entry = self.get(entry_id)
            del self._entries[entry_id]
            await self._async_commit({entry.date})
            return {"deleted": entry_id, "date": entry.date.isoformat(), "totals": self.totals(entry.date)}

    async def async_set_day_note(self, day: date, note: str | None) -> dict[str, Any]:
        text = (note or "").strip() or None
        async with self._lock:
            if text:
                self._notes[day] = text
            else:
                self._notes.pop(day, None)
            await self._async_commit({day})
            return {"date": day.isoformat(), "note": text}

    async def async_import(self, data: dict[str, Any], *, replace: bool) -> dict[str, int]:
        if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
            raise InvalidInput(f"not an export of this integration (format must be {EXPORT_FORMAT!r})")
        today = self._today()
        now = dt_util.utcnow()
        entries: dict[str, FoodEntry] = {}
        for number, raw in enumerate(data.get("entries") or [], 1):
            try:
                content = {k: raw.get(k) for k in ("date", "time", "category", "description", "kcal",
                                                   "protein_g", "sat_fat_g", "alcohol_units") if k in raw}
                fields = validate_fields(content, partial=False, today=today)
                if "date" not in fields:
                    raise InvalidInput("missing required field(s): date")
                entry_id = raw.get("id") if isinstance(raw.get("id"), str) and raw.get("id") else ulid_now()
                entries[entry_id] = FoodEntry(
                    id=entry_id, date=fields["date"], category=fields["category"],
                    description=fields["description"], kcal=fields["kcal"], time=fields.get("time"),
                    protein_g=fields.get("protein_g"), sat_fat_g=fields.get("sat_fat_g"),
                    alcohol_units=fields.get("alcohol_units"), source=str(raw.get("source") or "service"),
                    created_at=_parse_dt(raw.get("created_at"), now), updated_at=_parse_dt(raw.get("updated_at"), now),
                )
            except (InvalidInput, AttributeError) as exc:
                raise InvalidInput(f"entry {number}: {exc}") from None
        notes: dict[date, str] = {}
        for key, text in (data.get("notes") or {}).items():
            day = parse_date(key, "note date", today)
            if day is not None and isinstance(text, str) and text.strip():
                notes[day] = text.strip()
        async with self._lock:
            if (self._entries or self._notes) and not replace:
                raise InvalidInput("log is not empty; pass replace: true to overwrite it")
            changed = self.days_with_entries() | set(self._notes) | {e.date for e in entries.values()} | set(notes)
            self._entries, self._notes = entries, notes
            await self._async_commit(changed)
            return {"entries": len(entries), "notes": len(notes)}


def _same_item(a: FoodEntry, b: FoodEntry) -> bool:
    return a.category == b.category and a.kcal == b.kcal and a.description.casefold() == b.description.casefold()


def _parse_dt(value: Any, default: datetime) -> datetime:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else default
    except ValueError:
        return default
