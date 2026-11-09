from datetime import UTC, date, datetime, time

import pytest

from custom_components.ha_nutrition.models import (
    FoodEntry,
    InvalidInput,
    day_totals,
    validate_fields,
)

TODAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)


def entry(category="breakfast", kcal=400.0, entry_time=None, **kw) -> FoodEntry:
    return FoodEntry(
        id=kw.pop("id", "01ABC"),
        date=kw.pop("day", TODAY),
        category=category,
        description=kw.pop("description", "Gröt"),
        kcal=kcal,
        time=entry_time,
        protein_g=kw.pop("protein_g", None),
        sat_fat_g=kw.pop("sat_fat_g", None),
        alcohol_units=kw.pop("alcohol_units", None),
        source="llm",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize("entry_time", [None, time(7, 30)])
def test_round_trip(entry_time):
    e = entry(entry_time=entry_time, protein_g=30.0)
    d = e.to_dict()
    assert d["date"] == "2026-11-09"
    assert d["time"] == ("07:30" if entry_time else None)
    assert FoodEntry.from_dict(d) == e


def test_validate_full_entry():
    out = validate_fields(
        {"category": "snack", "description": " fika ", "kcal": 250, "protein_g": 4}, partial=False, today=TODAY
    )
    assert out == {"category": "snack", "description": "fika", "kcal": 250.0, "protein_g": 4.0}


def test_validate_converts_date_and_time():
    out = validate_fields({"date": "2026-11-08", "time": "07:05"}, partial=True, today=TODAY)
    assert out == {"date": date(2026, 11, 8), "time": time(7, 5)}


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"category": "fika", "description": "x", "kcal": 1}, "category"),
        ({"category": "snack", "description": "x", "kcal": -1}, "kcal"),
        ({"category": "snack", "description": "x", "kcal": 10001}, "kcal"),
        ({"category": "snack", "description": "  ", "kcal": 1}, "description"),
        ({"category": "snack", "description": "x" * 501, "kcal": 1}, "description"),
        ({"category": "snack", "description": "x", "kcal": 1, "carbs_g": 3}, "carbs_g"),
        ({"category": "snack", "description": "x", "kcal": 1, "time": "7.30"}, "time"),
        ({"category": "snack", "description": "x"}, "kcal"),
        ({"category": "snack", "description": "x", "kcal": 1, "date": "2026-11-10"}, "date"),
        ({"category": "snack", "description": "x", "kcal": 1, "date": "yesterday"}, "date"),
        ({"category": "snack", "description": "x", "kcal": True}, "kcal"),
        ({"category": "snack", "description": "x", "kcal": 1, "protein_g": -2}, "protein_g"),
        ({"category": "snack", "description": "x", "kcal": float("nan")}, "kcal"),
        ({"category": "snack", "description": "x", "kcal": float("inf")}, "kcal"),
        ({"category": "snack", "description": "x", "kcal": 1, "protein_g": float("nan")}, "protein_g"),
        ({"category": "snack", "description": "x", "kcal": 1, "protein_g": 1001}, "protein_g"),
        ({"category": "snack", "description": "x", "kcal": 1, "alcohol_units": 101}, "alcohol_units"),
    ],
)
def test_validate_rejects(fields, field):
    with pytest.raises(InvalidInput, match=field):
        validate_fields(fields, partial=False, today=TODAY)


def test_partial_allows_subset():
    assert validate_fields({"kcal": 300}, partial=True, today=TODAY) == {"kcal": 300.0}


def test_optional_nutrient_can_be_cleared():
    assert validate_fields({"protein_g": None}, partial=True, today=TODAY) == {"protein_g": None}


def test_day_totals():
    entries = [
        entry("breakfast", 400, protein_g=30.0),
        entry("drink", 120, alcohol_units=1.0),
        entry("drink", 120, alcohol_units=1.0),
    ]
    t = day_totals(TODAY, entries)
    assert (t.entries, t.kcal, t.protein_g, t.sat_fat_g, t.alcohol_units) == (3, 640, 30, 0, 2)
    assert t.kcal_by_category == {"breakfast": 400, "drink": 240}
    assert t.to_dict()["date"] == "2026-11-09"
