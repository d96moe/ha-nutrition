"""LLM API "Nutrition": food-logging tools for AI agents.

Home Assistant's MCP server publishes every registered LLM API at its own URL,
/api/mcp/<api id>, so an MCP client can be given this API alone: it
sees the food log and goals and nothing else in Home Assistant.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from .burn import async_burn_source, async_daily_burn
from .const import CATEGORIES, DOMAIN, SOURCE_LLM
from .models import DayTotals, InvalidInput, day_totals, parse_date
from .number import GOALS, goal, goal_entity_ids

try:  # Home Assistant 2026.10+ describes tool parameters with probatio
    import probatio as schema_lib
except ImportError:  # older releases use voluptuous
    import voluptuous as schema_lib  # type: ignore[no-redef]

if TYPE_CHECKING:
    from . import NutritionConfigEntry

API_NAME = "Nutrition"
PERIODS = ("day", "week", "month")
MAX_DAYS = 400

API_PROMPT = (
    "The user's food log in Home Assistant. Log what the user says they ate or drank with "
    "log_food: split one message into one entry per item, each with a category: breakfast, "
    "lunch, dinner, snack (fika, cakes, sweets, crisps), drink (wine, beer and other drinks; "
    "set alcohol_units, e.g. 1 per glass of wine or 33 cl beer), supplement (protein shake etc.) "
    "or other. kcal, protein_g and sat_fat_g are your estimates; that is fine, but don't invent "
    "a nutrient you have no basis for (leave it out). Send a request_id that is unique to the "
    "user message (e.g. a UUID or timestamp, never reused across conversations) so a retried "
    "call doesn't log twice; a result with repeated=true means that call was already logged and "
    "nothing new was written. Check possible_duplicates in the result. Leave date out for "
    "today. Dates are "
    "YYYY-MM-DD in the user's local time zone; default is today. Past days may be logged "
    "(backfill), future days not: before backfilling, call get_nutrition and skip days listed "
    "in logged_days unless the user wants them changed. To correct or remove an entry, find its "
    "id with get_nutrition include_entries=true, then update_entry or delete_entry. Goals "
    "(get_goals/set_goals): in set_goals, 0 clears a goal; get_goals shows an unset goal as null."
)

_READ_ONLY: dict[str, Any] = {}
_WRITE: dict[str, Any] = {}
_DESTRUCTIVE: dict[str, Any] = {}
if hasattr(llm, "ToolAnnotations"):  # 2026.10+: forwarded to MCP clients as tool hints
    _READ_ONLY = {"annotations": llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False)}
    _WRITE = {"annotations": llm.ToolAnnotations(
        read_only=False, destructive=False, idempotent=False, open_world=False)}
    _DESTRUCTIVE = {"annotations": llm.ToolAnnotations(
        read_only=False, destructive=True, idempotent=True, open_world=False)}

NUMBER = schema_lib.Any(int, float)
OPT_NUMBER = schema_lib.Any(None, int, float)
_DATE = "YYYY-MM-DD, local time"


class ToolError(Exception):
    """A problem to report back to the agent as the tool's result."""


def _result(data: dict[str, Any], error: bool = False):
    if hasattr(llm, "ToolResult"):
        return llm.ToolResult(data=data, error=error)
    return data


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _today() -> date:
    return dt_util.now().date()


def _date(value: str | None, name: str) -> date | None:
    return parse_date(value, name, _today())


ENTRY_FIELDS = {
    schema_lib.Required("category", description="Where it belongs"): schema_lib.In(list(CATEGORIES)),
    schema_lib.Required("description", description="What it was, as the user would say it"): str,
    schema_lib.Required("kcal", description="Estimated kcal"): NUMBER,
    schema_lib.Optional("time", description="HH:MM, when eaten; optional"): str,
    schema_lib.Optional("protein_g", description="Estimated protein, grams"): NUMBER,
    schema_lib.Optional("sat_fat_g", description="Estimated saturated fat, grams"): NUMBER,
    schema_lib.Optional("alcohol_units", description="Glasses of wine / beers etc."): NUMBER,
}


def period_start(day: date, period: str) -> date:
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def goals_view(entry: NutritionConfigEntry) -> dict[str, Any]:
    return {key: {"value": goal(entry, key), "unit": spec.unit} for key, spec in GOALS.items()}


class _Tool(llm.Tool):
    hints: dict[str, Any] = _READ_ONLY

    def __init__(self, entry: NutritionConfigEntry) -> None:
        self.entry = entry
        self.integration = DOMAIN
        for key, value in self.hints.items():
            setattr(self, key, value)

    @property
    def store(self):
        return self.entry.runtime_data.store

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        try:
            return _result(await self._async_run(hass, dict(tool_input.tool_args)))
        except (ToolError, InvalidInput) as exc:
            return _result({"error": str(exc)}, error=True)

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class LogFoodTool(_Tool):
    name = "log_food"
    hints = _WRITE
    description = (
        "Log one or more food/drink entries for one day (default today). Returns the stored "
        "entries with their ids, the day's new totals and possible_duplicates (same item already "
        "logged that day)."
    )
    parameters = schema_lib.Schema({
        schema_lib.Optional("date", description=f"{_DATE}; default today; not in the future"): str,
        schema_lib.Required("entries", description="One entry per item eaten or drunk (1-50)"): [
            schema_lib.Schema(ENTRY_FIELDS)
        ],
        schema_lib.Optional("request_id", description="Unique per user message; a repeat is a no-op"): str,
    })

    async def _async_run(self, hass, args):
        return await self.store.async_log_entries(
            _date(args.get("date"), "date"), args["entries"], source=SOURCE_LLM, request_id=args.get("request_id")
        )


class UpdateEntryTool(_Tool):
    name = "update_entry"
    hints = _WRITE
    description = "Change some fields of one logged entry (get ids from get_nutrition include_entries=true)."
    parameters = schema_lib.Schema({
        schema_lib.Required("entry_id"): str,
        schema_lib.Optional("date", description=f"{_DATE}; moves the entry to that day"): str,
        schema_lib.Optional("time", description="HH:MM, or null to clear"): schema_lib.Any(None, str),
        schema_lib.Optional("category"): schema_lib.In(list(CATEGORIES)),
        schema_lib.Optional("description"): str,
        schema_lib.Optional("kcal"): NUMBER,
        schema_lib.Optional("protein_g", description="null clears it"): OPT_NUMBER,
        schema_lib.Optional("sat_fat_g", description="null clears it"): OPT_NUMBER,
        schema_lib.Optional("alcohol_units", description="null clears it"): OPT_NUMBER,
    })

    async def _async_run(self, hass, args):
        entry_id = args.pop("entry_id")
        return await self.store.async_update_entry(entry_id, args, source=SOURCE_LLM)


class DeleteEntryTool(_Tool):
    name = "delete_entry"
    hints = _DESTRUCTIVE
    description = "Delete one logged entry. Returns its day's new totals."
    parameters = schema_lib.Schema({schema_lib.Required("entry_id"): str})

    async def _async_run(self, hass, args):
        return await self.store.async_delete_entry(args["entry_id"])


class SetDayNoteTool(_Tool):
    name = "set_day_note"
    hints = _WRITE
    description = "Set a free-text note for a day (context such as 'party', 'ill'); an empty note clears it."
    parameters = schema_lib.Schema({
        schema_lib.Required("date", description=_DATE): str,
        schema_lib.Optional("note"): str,
    })

    async def _async_run(self, hass, args):
        day = _date(args["date"], "date")
        if day is None:
            raise ToolError("date is required")
        return await self.store.async_set_day_note(day, args.get("note"))


class GetNutritionTool(_Tool):
    name = "get_nutrition"
    description = (
        "Intake per day, week or month between two dates: kcal, protein, saturated fat, alcohol, "
        "kcal per category, day notes, and burn (total calories burned, when available) with "
        "net = intake - burn over the days that have both. logged_days/unlogged_days tell which "
        "days have entries (use before backfilling). include_entries adds every entry with its id."
    )
    parameters = schema_lib.Schema({
        schema_lib.Optional("start", description=f"{_DATE}; default 6 days before end"): str,
        schema_lib.Optional("end", description=f"{_DATE}; default today"): str,
        schema_lib.Optional("period", description="day, week or month; default day"): schema_lib.In(list(PERIODS)),
        schema_lib.Optional("include_entries", description="Add the entries with ids; default false"): bool,
    })

    async def _async_run(self, hass, args):
        end = _date(args.get("end"), "end") or _today()
        start = _date(args.get("start"), "start") or end - timedelta(days=6)
        if start > end:
            raise ToolError("start is after end")
        if (end - start).days + 1 > MAX_DAYS:
            raise ToolError(f"at most {MAX_DAYS} days per call; split the range")
        period = args.get("period", "day")
        statistic = await async_burn_source(hass, self.entry.options)
        try:
            burn = await async_daily_burn(hass, statistic, start, end)
        except HomeAssistantError:
            burn = {}
        store = self.store
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        logged = store.days_with_entries()
        notes = store.notes_between(start, end)
        rows: dict[date, dict[str, Any]] = {}
        for day in days:
            key = period_start(day, period)
            row = rows.setdefault(key, {"_totals": DayTotals(date=key), "days_logged": 0, "burn": None,
                                        "net": None, "notes": {}, "entries": []})
            entries = store.entries_on(day)
            t = day_totals(day, entries)
            acc: DayTotals = row["_totals"]
            acc.kcal += t.kcal
            acc.protein_g += t.protein_g
            acc.sat_fat_g += t.sat_fat_g
            acc.alcohol_units += t.alcohol_units
            acc.entries += t.entries
            for c, v in t.kcal_by_category.items():
                acc.kcal_by_category[c] = acc.kcal_by_category.get(c, 0.0) + v
            if entries:
                row["days_logged"] += 1
            if (b := burn.get(day)) is not None:
                row["burn"] = (row["burn"] or 0.0) + b
                if entries:
                    row["net"] = (row["net"] or 0.0) + t.kcal - b
            if day in notes:
                row["notes"][day.isoformat()] = notes[day]
            row["entries"].extend(e.to_dict() for e in entries)
        out_rows = []
        for key, row in rows.items():
            totals = row["_totals"].to_dict()
            out = {
                "period_start": key.isoformat(),
                "days_logged": row["days_logged"],
                "kcal": totals["kcal"],
                "protein_g": totals["protein_g"],
                "sat_fat_g": totals["sat_fat_g"],
                "alcohol_units": totals["alcohol_units"],
                "kcal_by_category": totals["kcal_by_category"],
                "burn_kcal": _r(row["burn"], 0),
                "net_kcal": _r(row["net"], 0),
            }
            if row["notes"]:
                out["notes"] = row["notes"]
            if args.get("include_entries"):
                out["entries"] = row["entries"]
            out_rows.append(out)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "period": period,
            "rows": out_rows,
            "logged_days": [d.isoformat() for d in days if d in logged],
            "unlogged_days": [d.isoformat() for d in days if d not in logged],
            "burn_source": statistic or None,
        }


class GetGoalsTool(_Tool):
    name = "get_goals"
    description = "The user's goals; value null means not set."
    parameters = schema_lib.Schema({})

    async def _async_run(self, hass, args):
        return goals_view(self.entry)


class SetGoalsTool(_Tool):
    name = "set_goals"
    hints = _WRITE
    description = "Change one or more goals; 0 clears a goal. Returns all goals."
    parameters = schema_lib.Schema({
        schema_lib.Optional(key, description=f"{spec.unit}, 0-{spec.max:g}"): NUMBER
        for key, spec in GOALS.items()
    })

    async def _async_run(self, hass, args):
        for key, value in args.items():
            if not 0 <= value <= GOALS[key].max:
                raise ToolError(f"{key} must be between 0 and max {GOALS[key].max:g}, got {value}")
        entity_ids = goal_entity_ids(hass, self.entry)
        for key, value in args.items():
            await hass.services.async_call(
                "number", "set_value", {"entity_id": entity_ids[key], "value": value}, blocking=True
            )
        return goals_view(self.entry)


TOOLS = (LogFoodTool, UpdateEntryTool, DeleteEntryTool, SetDayNoteTool, GetNutritionTool, GetGoalsTool, SetGoalsTool)


class NutritionAPI(llm.API):
    """Registered by the config entry (single_config_entry)."""

    def __init__(self, hass: HomeAssistant, entry: NutritionConfigEntry) -> None:
        super().__init__(hass=hass, id=DOMAIN, name=API_NAME)
        self.entry = entry

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(
            api=self, api_prompt=API_PROMPT, llm_context=llm_context, tools=[cls(self.entry) for cls in TOOLS]
        )
