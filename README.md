# Nutrition for Home Assistant

A custom integration that keeps a **food log in Home Assistant**. Entries come in
through its services (automations, scripts, Developer tools) or, if you connect one,
from an AI agent over MCP that turns "porridge for breakfast, chicken stew for lunch,
two glasses of wine" into entries. You get today's intake,
protein and alcohol as sensors, net balance and budget left against your calorie burn,
daily totals as long-term statistics, and goals you can change from a dashboard.

It stores what it is told. It has no food database: estimating calories is the
agent's job.

## What you get

| Group | Entities | Notes |
|---|---|---|
| Today | Intake today, Protein today, Saturated fat today, Alcohol today, Alcohol this week | Start over at local midnight; the week starts on Monday |
| Balance | Net today, Budget left today | Need a burn sensor (and BMR + deficit goal for budget); see [Balance](#balance) |
| Goals | Deficit target, Protein floor, Protein ceiling, Target weight, Alcohol limit per week | Number entities; **0 means not set** |
| Progress | Protein left today, To target weight | Need the matching goal (and a weight sensor) |
| History | Statistics `ha_nutrition:kcal`, `:protein`, `:sat_fat`, `:alcohol`, `:kcal_<category>` | One value per day; see [History](#history) |
| AI agents | LLM API *Nutrition* (MCP) | See [AI agents](#ai-agents-mcp-the-nutrition-api) |

Every entry has a **category**: `breakfast`, `lunch`, `dinner`, `snack` (fika,
cakes, sweets), `drink` (wine, beer and other drinks), `supplement` (protein shake
and similar) or `other`. It has a description and kcal, and optionally a time,
protein, saturated fat and alcohol units.

## Requirements

- **Home Assistant 2026.10 or newer.**
- For net balance and budget: a sensor with today's total calorie burn so far, and
  one with your BMR. No particular integration is needed; see [Burn, BMR and weight
  sources](#burn-bmr-and-weight-sources).

## Burn, BMR and weight sources

Nutrition doesn't depend on any health integration: in the options you pick any
sensors, from whichever integration you use (Withings, Garmin, Fitbit, the Companion
app's Health Connect sensors, a template sensor, ...).

- **Burn today**: today's total calories burned so far (kcal).
- **BMR**: basal metabolic rate (kcal/day), to forecast the rest of the day.
- **Weight**: current weight (kg).
- **Burn per day in the past** (for `get_nutrition`): the *burn statistic* option, if
  set. Otherwise, if the burn sensor has `state_class` `total` or `total_increasing`
  (most daily-total sensors do), its own long-term statistics are used. Otherwise
  past days have no burn and no net.
- **Yesterday's value after midnight**: if the burn sensor has a `date` attribute,
  it must be today. If it hasn't, a value that hasn't changed since local midnight is
  taken as yesterday's. Either way net and budget are unavailable until the source
  updates, instead of showing a huge surplus. (Right after a Home Assistant restart
  a source's old value can look fresh until it updates.)

The [Samsung Health](https://github.com/d96moe/ha-samsung-health) integration is
pre-filled when it is set up: its burn, BMR and weight sensors, and its
`ha_samsung_health:total_calories` statistic when that exists (its sensors reset at
midnight without a `state_class`, so the statistic carries the history).

## Installation

1. In Home Assistant, open Marketplace (formerly HACS) and add this repository as a
   custom repository (type *Integration*).
2. Install **Nutrition** and restart Home Assistant.
3. Go to Settings → Devices & services → Add integration → **Nutrition**.
4. Pick the burn, BMR and weight sensors (all optional). You can change them later
   under *Configure*.

## AI agents (MCP): the Nutrition API

The integration registers an LLM API called **Nutrition**. Home Assistant's Model
Context Protocol server publishes it at its own URL, `/api/mcp/ha_nutrition`. An agent
connected to that URL sees these tools and nothing else in Home Assistant:

| Tool | Does |
|---|---|
| `log_food` | Adds one or more entries for one day (default today). Returns them with ids, the day's totals and possible duplicates. |
| `update_entry` | Changes fields of one entry, including moving it to another day. |
| `delete_entry` | Deletes one entry. |
| `set_day_note` | Sets or clears a free-text note for a day ("party", "ill"). |
| `get_nutrition` | Totals per day, week or month between two dates, per category, with notes, burn and net; which days are logged; optionally every entry with its id. |
| `get_goals` / `set_goals` | Reads or changes the goals. |

### Connecting an AI agent (optional)

Using the API is optional; the services do the same. If you want an AI agent or
assistant to log food and answer questions about it, connect any client that
supports remote MCP servers to the API's URL:

1. In Home Assistant, open **Settings → AI**. Under *Model Context Protocol →
   Individual URLs*, **Nutrition** is listed. Copy its URL.
2. Add that URL as an MCP server in your client. A client outside your home network
   needs Home Assistant's remote address, for example
   `https://<your-id>.ui.nabu.casa/api/mcp/ha_nutrition` with Home Assistant Cloud
   (Nabu Casa), or another public HTTPS address.
3. Sign in to Home Assistant when the client asks.

Home Assistant only allows **administrators** to use the individual API URLs, so sign
in with an admin user. Through that URL the client can only call the tools above; the
sign-in token, however, is an ordinary Home Assistant token for that user.

The API's own instructions tell the agent how to pick categories, to split a message
into one entry per item, and to send a `request_id`. In your agent's instructions,
ask it to log what you tell it you ate.

For example, in **claude.ai**: open *Settings → Connectors → Add custom connector*,
paste the remote URL and sign in to Home Assistant; then, in a project's
instructions, ask it to log each meal with `log_food`.

### Safe to retry, hard to corrupt

- **Retries don't double-log.** With the same `request_id`, a repeated `log_food`
  returns the first result (`repeated: true`) and writes nothing. Request ids are kept
  for 7 days. An id reused for a *different* day is refused rather than taken as a
  retry, so a reused id can't silently swallow a meal.
- **Duplicates are flagged, not dropped.** Two coffees are real, so identical entries
  are stored, but the result lists `possible_duplicates` (same day, category,
  description and kcal) so the agent can ask.
- **Bad input is rejected, not guessed:** an unknown category, a negative,
  non-numeric or implausible value (over 10,000 kcal, 1,000 g protein or saturated
  fat, or 100 alcohol units per entry), a future date or an unknown field gives an
  error that says what's wrong, and nothing from that call is stored.
- **An empty log at startup leaves the statistics alone.** If the log file were lost
  or corrupt, the daily statistics are not cleared to match it.

## Logging past days

Any past date can be logged, corrected or deleted, like today. To fill in history from
an older log (a notes file, a spreadsheet), give it to your agent and ask it to log
each day: `get_nutrition` lists `logged_days` and `unlogged_days`, so it can skip days
already in Home Assistant.

## Balance

- **Net today** = intake today − burn so far.
- **Budget left today** = projected burn − deficit target − intake today, where
  projected burn = burn so far + BMR × the share of the day still ahead. It's a
  forecast (attribute `forecast: true`, with `projected_burn`), and it moves with the
  clock: updated every 5 minutes and whenever something changes.
- If the burn sensor still shows yesterday's total after midnight, these sensors are
  unavailable until it catches up (see [sources](#burn-bmr-and-weight-sources)).

## Goals

The goals are number entities on the *Nutrition* device: set them from a dashboard,
an automation (`number.set_value`) or by asking the agent (`set_goals`). Their changes
are kept in the recorder's history (see [Recorder](#recorder)), so you can see later
which goal applied when. **0 means not set**, and sensors that need an unset goal are
unavailable.

## History

Daily totals are long-term statistics, one value per day, for every day that has
entries. A day without entries has no value, so *not logged* stays different from
*ate nothing*. Logging, correcting or deleting a past day rewrites its statistics at
once (deleting the last entry of a day removes that day's value). Show them as daily
bars:

```yaml
type: statistics-graph
title: Intake per day
entities:
  - ha_nutrition:kcal
period: day
stat_types:
  - change
chart_type: bar
days_to_show: 30
```

The *today* sensors have no long-term statistics of their own: they only describe
today, and past days are logged into the statistics above instead.

## Recorder

If your recorder uses an `include` list, add the integration's entities so goal
changes and intra-day values are kept:

```yaml
recorder:
  include:
    entity_globs:
      - sensor.*nutrition_*
      - number.*nutrition_*
```

The recorder has no reload: restart Home Assistant after changing this.

## Your data

- The log is stored in `/config/.storage/ha_nutrition.log` and is part of Home
  Assistant's backups.
- **Removing the integration keeps the log** (and its statistics). Adding it again
  picks it up where it was.
- `ha_nutrition.export` returns the whole log as JSON; `ha_nutrition.import` restores
  one (refused if the log isn't empty, unless `replace: true`).
- Nothing leaves Home Assistant except what an agent you connected reads with the
  tools above.

## Services

`ha_nutrition.log_food`, `update_entry`, `delete_entry`, `set_day_note`, `export` and
`import` take the same fields as the tools and return the same results (response
data), for automations and Developer tools. Errors are validation errors with the
same messages.

## Development

```
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest tests --timeout 30
```

The Home Assistant test plugin needs Linux, macOS or WSL. See [ci/README.md](ci/README.md).

## License

MIT
