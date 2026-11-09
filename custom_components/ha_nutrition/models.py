"""Food entries, input validation and day totals. Pure: no Home Assistant imports."""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from .const import CATEGORIES, MAX_DESCRIPTION, MAX_KCAL_PER_ENTRY, NUTRIENT_FIELDS


class InvalidInput(ValueError):
    """Input that is rejected instead of guessed. The message is shown to the caller."""


@dataclass(slots=True)
class FoodEntry:
    id: str
    date: date
    category: str
    description: str
    kcal: float
    time: time | None
    protein_g: float | None
    sat_fat_g: float | None
    alcohol_units: float | None
    source: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "time": self.time.strftime("%H:%M") if self.time else None,
            "category": self.category,
            "description": self.description,
            "kcal": self.kcal,
            "protein_g": self.protein_g,
            "sat_fat_g": self.sat_fat_g,
            "alcohol_units": self.alcohol_units,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FoodEntry:
        return cls(
            id=d["id"],
            date=date.fromisoformat(d["date"]),
            category=d["category"],
            description=d["description"],
            kcal=float(d["kcal"]),
            time=time.fromisoformat(d["time"]) if d.get("time") else None,
            protein_g=_opt_float(d.get("protein_g")),
            sat_fat_g=_opt_float(d.get("sat_fat_g")),
            alcohol_units=_opt_float(d.get("alcohol_units")),
            source=d.get("source", "service"),
            created_at=datetime.fromisoformat(d["created_at"]),
            updated_at=datetime.fromisoformat(d["updated_at"]),
        )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def parse_date(value: str | None, name: str, today: date) -> date | None:
    """YYYY-MM-DD, not after today. None/"" -> None."""
    if value in (None, ""):
        return None
    try:
        day = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        day = None
    if day is None:
        raise InvalidInput(f"{name} must be a date like 2026-11-08, got {value!r}")
    if day > today:
        raise InvalidInput(f"{name} {day.isoformat()} is in the future (today is {today.isoformat()})")
    return day


def _parse_time(value: Any) -> time | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str) and len(value) == 5 and value[2] == ":":
            return time.fromisoformat(value)
    except ValueError:
        pass
    raise InvalidInput(f"time must be HH:MM (24 h), got {value!r}")


def _number(value: Any, name: str, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidInput(f"{name} must be a number, got {value!r}")
    if value < 0:
        raise InvalidInput(f"{name} can't be negative, got {value}")
    if value > maximum:
        raise InvalidInput(f"{name} {value} is more than {maximum:g}; split it or check the estimate")
    return float(value)


#: Per-entry ceilings: above these it's an error, not a meal (also keeps NaN-like junk out).
NUTRIENT_MAX = {"protein_g": 1000, "sat_fat_g": 1000, "alcohol_units": 100}

ALLOWED_FIELDS =("date", "time", "category", "description", "kcal", *NUTRIENT_FIELDS)
REQUIRED_FIELDS = ("category", "description", "kcal")


def validate_fields(fields: dict[str, Any], *, partial: bool, today: date) -> dict[str, Any]:
    """Normalised copy of `fields`, or InvalidInput naming the bad field."""
    if unknown := sorted(set(fields) - set(ALLOWED_FIELDS)):
        raise InvalidInput(f"unknown field(s) {', '.join(unknown)}; allowed: {', '.join(ALLOWED_FIELDS)}")
    if not partial and (missing := [f for f in REQUIRED_FIELDS if fields.get(f) is None]):
        raise InvalidInput(f"missing required field(s): {', '.join(missing)}")
    out: dict[str, Any] = {}
    for name, value in fields.items():
        if name == "date":
            if (day := parse_date(value, "date", today)) is not None:
                out["date"] = day
        elif name == "time":
            out["time"] = _parse_time(value)
        elif name == "category":
            if value not in CATEGORIES:
                raise InvalidInput(f"category must be one of {', '.join(CATEGORIES)}, got {value!r}")
            out["category"] = value
        elif name == "description":
            text = value.strip() if isinstance(value, str) else ""
            if not text:
                raise InvalidInput("description can't be empty")
            if len(text) > MAX_DESCRIPTION:
                raise InvalidInput(f"description is longer than {MAX_DESCRIPTION} characters")
            out["description"] = text
        elif name == "kcal":
            out["kcal"] = _number(value, "kcal", MAX_KCAL_PER_ENTRY)
        else:  # optional nutrient; None clears it
            out[name] = None if value is None else _number(value, name, NUTRIENT_MAX[name])
    return out


@dataclass(slots=True)
class DayTotals:
    date: date
    entries: int = 0
    kcal: float = 0.0
    protein_g: float = 0.0
    sat_fat_g: float = 0.0
    alcohol_units: float = 0.0
    kcal_by_category: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "entries": self.entries,
            "kcal": round(self.kcal, 1),
            "protein_g": round(self.protein_g, 1),
            "sat_fat_g": round(self.sat_fat_g, 1),
            "alcohol_units": round(self.alcohol_units, 2),
            "kcal_by_category": {c: round(v, 1) for c, v in self.kcal_by_category.items()},
        }


def day_totals(day: date, entries: Iterable[FoodEntry]) -> DayTotals:
    totals = DayTotals(date=day)
    for e in entries:
        totals.entries += 1
        totals.kcal += e.kcal
        totals.protein_g += e.protein_g or 0.0
        totals.sat_fat_g += e.sat_fat_g or 0.0
        totals.alcohol_units += e.alcohol_units or 0.0
        totals.kcal_by_category[e.category] = totals.kcal_by_category.get(e.category, 0.0) + e.kcal
    totals.kcal_by_category = {c: totals.kcal_by_category[c] for c in CATEGORIES if c in totals.kcal_by_category}
    return totals
