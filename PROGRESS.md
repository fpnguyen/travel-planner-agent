# Travel Planner Agent — Progress Summary

## Status: Complete scaffold, ready for testing

### 2026-09-13: Coordinator miscalculated "next year"

User asked for a trip "next year"; the coordinator resolved it to a date in
`2026` — the *current* year, not next. Root cause: Gemini has no reliable
built-in sense of the current date, and nothing in the coordinator's
instruction ever told it — `root_agent.instruction` was a static string
built once at module import time, so even if we'd hardcoded a date into it,
that date would go stale on a long-running `adk web` process without a
restart (and we already found plenty of multi-day-old leftover processes
from earlier testing).

Fixed by converting `root_agent`'s instruction from a plain string into an
`InstructionProvider` callable (`_build_root_instruction`) — confirmed this
is a real, first-class option by checking `LlmAgent.model_fields['instruction'].annotation`
directly: `Union[str, Callable[[ReadonlyContext], Union[str, Awaitable[str]]]]`.
ADK calls this fresh on every request rather than once at import time, so
`date.today()` is computed at the moment each conversation turn actually
happens. The function opens with an explicit "Today's date is {today};
ground every relative date against this yourself, don't rely on your own
sense of the current date" instruction.

Verified directly: called `_build_root_instruction(None)` standalone and
confirmed (a) it returns today's real date correctly, and (b) the `{user:
interests?}` -style placeholders still pass through as literal text for
ADK's own downstream state-injection to resolve later (this needed checking
since moving to an f-string *inside* a function is an easy place to
accidentally break the double-brace escaping). `adk web` still boots clean
with the new callable-based instruction.

This fix only applies to the root coordinator, which is the one place
relative-date language like "next year" actually gets interpreted —
`booking_agent`/`itinerary_planner_agent`/`packing_agent`/
`destination_recommender_agent` all receive already-resolved absolute
YYYY-MM-DD dates from the coordinator, so they don't independently need to
know "today."

### 2026-09-13: Two real bugs from first live use

**1. `PydanticSerializationError: Unable to serialize unknown type: <class 'coroutine'>`**
`export_trip_plan_to_excel` called `tool_context.save_artifact(...)` without
`await`. Checked directly: `inspect.iscoroutinefunction(ToolContext.save_artifact)`
→ `True` — it's async. Without awaiting it, the call returns an unawaited
coroutine object instead of the actual version int, and that coroutine ends
up inside the tool's return dict, which is what ADK then fails to serialize.
Fixed by making `export_trip_plan_to_excel` itself `async def` and awaiting
the call. Re-verified the tool's declared schema is unaffected (`tool_context`
still correctly excluded from what the LLM sees) after the change.

**2. Duffel `422` misread as "no flights exist for this route"**
User saw a 422 for DFW→PEK/PKX and asked if that meant no flights existed
between those airports. Checked directly against the live Duffel API rather
than guessing: DFW→PEK, DFW→PKX, and a DFW↔PEK round trip *all* returned real
`201` offers immediately. A 422 is Duffel's signal that **the request itself
was invalid** — a genuinely empty search comes back as `200` with an empty
`offers` list, not a 422. So the premise was wrong; the actual bug was that
`duffel_client.py`'s `response.raise_for_status()` discards the response
body, which is exactly where Duffel puts the specific reason. Confirmed by
deliberately triggering a real 422 (a past departure date) and inspecting the
body directly:
```json
{"errors": [{"source": {"field": "departure_date"}, "message": "Field 'departure_date' must be after 2026-09-11", "code": "invalid_date"}]}
```
Fixed `duffel_client.py` to catch the `HTTPError` and re-raise with that
detail extracted (field + message), so `search_flights`'s `error_message` is
now specific instead of a bare "422 Client Error: Unprocessable Entity."
Applied the identical fix to `places_client.py` (same swallowed-body pattern,
Google's own `{"error": {"message": ...}}` shape). Also added an explicit
instruction to `booking_agent`: a `search_flights` error means the request
was rejected, not that the route has no availability — don't conflate the two
regardless of what the error text says.

Re-verified both fixes against the live APIs (not mocked): the error path now
returns `"422 error from Duffel: departure_date: Field 'departure_date' must
be after 2026-09-11"` instead of the old generic message, and the success
path (a valid date) still returns 5 real offers as before.

### 2026-09-12: Weather-aware packing + Excel export

Two new connectors, both verified against live/real data before wiring into
agents (no mocks):

**Packing (`travel_planner/tools/weather_tools.py` + `sub_agents/packing_agent/`)**
- `get_weather_outlook(lat, lon, start_date, end_date)` calls Open-Meteo (no
  API key needed for non-commercial use, confirmed from their docs).
- Trip within ~15 days → real forecast API (`api.open-meteo.com/v1/forecast`).
- Trip further out (the common case) → Open-Meteo's forecast doesn't exist
  yet, so this averages the same calendar dates from the last 3 years via
  their Historical Weather API (`archive-api.open-meteo.com/v1/archive`)
  instead, clearly labeled `"historical_average"` with a note that it's an
  estimate, not a forecast. Deliberately did *not* use Open-Meteo's separate
  "Climate API" — that's actually a climate-*projection* model output (named
  models like CMCC_CM2_VHR4, scenario horizons to 2050), the wrong shape for
  "what's typical weather here in October."
- Both branches tested against the real API (no key required) before writing
  a single line of agent code: near-term request correctly hit the forecast
  path, far-out request correctly hit the historical-average path and
  returned sensible numbers.
- `packing_agent` is a new third branch in the `Workflow`'s parallel fan-out
  (`(START, seed_node, (booking_node, itinerary_node, packing_node),
  trip_specialists_join, merge_node)`) — confirmed the graph rebuilds
  correctly with 3-way fan-out/fan-in by printing its nodes/edges directly.
  Renamed the join node `trip_specialists_join` (was `booking_itinerary_join`)
  since it now waits on three branches, not two.
- `plan_merger_agent` updated to read `{packing_result}` alongside the other
  two and include it in the final plan, forwarding the forecast-vs-estimate
  caveat verbatim rather than dropping it.

**Excel export (`travel_planner/tools/excel_export_tools.py`)**
- `export_trip_plan_to_excel(...)` builds a 3-sheet workbook (Overview,
  Itinerary, Packing List) with `openpyxl`, then saves it via
  `tool_context.save_artifact()` — confirmed this is the right mechanism by
  checking `ToolContext`'s actual methods (`save_artifact`, `load_artifact`,
  `list_artifacts`) directly, and confirmed `google.genai.types.Part.from_bytes(data=,
  mime_type=)` is the correct way to wrap binary content for it. Using the
  artifact system means the file shows up as a real downloadable attachment
  in `adk web`'s chat UI, not just a path on the server's local disk.
  - Tested the workbook-building logic directly (not through the LLM):
    wrote a real file, reopened it with `openpyxl.load_workbook`, and
    confirmed sheet names, cell values, and that a day dict with missing
    keys renders blank cells instead of raising.
  - Opt-in only — the coordinator's instructions say never to export
    automatically, only when the user asks to save/download/export.
- No new credentials needed for either feature: Open-Meteo requires no API
  key, and Excel export has no external dependency beyond the `openpyxl`
  package (added to `requirements.txt`).

### 2026-09-12: Cross-session user memory

Added `travel_planner/tools/memory_tools.py` (`remember_trip_preferences`,
`remember_completed_trip`) so the coordinator remembers a user's interests,
preferred travel class, flight stop tolerance, and past planned trips across
*separate* conversations, not just within one chat.

Built entirely on ADK's existing `user:`-prefixed session state — no new
infrastructure. Verified directly, not assumed:
- `SqliteSessionService` (what `adk web` uses by default for local
  persistence — confirmed by finding `create_local_database_session_service`
  in `google/adk/cli/.../local_storage.py`, which creates exactly this class
  at `.adk/session.db`) keeps `user:`-prefixed state in a separate table and
  re-merges it into every new session for that `user_id`.
- Proved this survives a full process restart, not just a new session: wrote
  `user:` state via one `SqliteSessionService` instance, dropped it, created
  a **brand-new** instance pointed at the same `.db` file (functionally
  identical to killing and restarting `adk web`), created a new session for
  the same user — the remembered facts were already in its initial state.
- Confirmed `adk web`'s browser UI hardcodes `userId="user"` (found by
  grepping the shipped bundled JS directly — no `crypto.randomUUID` or
  `localStorage` involved) — so refreshing the page keeps the same identity,
  which is what makes this testable through the UI at all.
- A tool gets write access via a `tool_context: ToolContext` parameter, which
  ADK auto-injects and hides from the schema the LLM sees (confirmed by
  inspecting `canonical_tools()`'s resolved declarations — `tool_context`
  never appears in either new tool's parameter list).
- Exercised both tool functions directly against a real `State` object
  (bypassing the LLM entirely) and confirmed correct writes, including that
  `remember_completed_trip` appends to a list rather than overwriting it.

Coordinator instructions now open with `{user:interests?}` /
`{user:preferred_travel_class?}` / `{user:flight_stop_tolerance?}` /
`{user:trip_history?}` (the trailing `?` makes each optional — empty string
if unset) so a returning user's profile is simply present in the prompt, and
call the two remember tools as a final, silent step after presenting a plan.

**Caveat**: this only works if the same `user_id` is used across sessions.
Fine for local `adk web` testing (hardcoded to `"user"`); a real multi-user
deployment would need your own auth layer supplying a stable per-user ID.

### 2026-09-12: Live testing — model name fix + rate limit finding

First live run (real Gemini/Duffel/Google Places credentials) surfaced two
things, both from real API responses, not guesses:
- `gemini-2.5-flash` (hardcoded in all 5 agents) returned `404 NOT_FOUND`:
  "This model is no longer available to new users... use models/gemini-3.6-flash."
  Updated across all agents.
- Free-tier Gemini quota is 5 requests/minute per model. A single trip-planning
  turn through this app needs far more than that (coordinator's own reasoning
  + booking_step's and itinerary_step's internal tool-calling turns, running
  concurrently, + plan_merge_step + coordinator's final synthesis — easily
  10-15+ calls). A full live run wasn't completed end-to-end as a result, but
  the stack trace during the attempt showed `run_llm_agent_as_node` actively
  executing one of the Workflow's node-wrapped agents — real confirmation the
  graph executes as designed, just data-starved on quota. Fix is enabling
  billing on the Gemini API key's project (no code change needed) — left to
  the user to do when ready.

### 2026-08-19: Migrated off Amadeus (Duffel + Google Places)

Amadeus fully decommissioned its self-service API portal on 2026-07-17
(announced February 2026; new registrations paused that spring) — confirmed
via [PhocusWire](https://www.phocuswire.com/amadeus-shut-down-self-service-apis-portal-developers)
and [Tripgic's migration guide](https://www.tripgic.com/playbook/amadeus-api-shutdown-migration/).
Every tool in this project ran on that sandbox, so this was a hard break, not
a gradual deprecation — existing self-service API keys stopped working.

Replaced:
- **Flights** (`resolve_city`, `search_flights`) → Duffel's Offer Requests +
  Places Suggestions APIs, called via plain `requests` (Duffel's official
  Python client is unmaintained — see `tools/duffel_client.py`).
- **Points of interest** (`search_points_of_interest`) → Google Places API
  (New) Nearby Search (`tools/places_client.py`).
- **Destination inspiration** (`search_destinations`) → dropped; no Duffel or
  Google equivalent exists for "suggest destinations by budget." Replaced
  with the LLM's own travel knowledge for candidate generation, grounded by a
  real Duffel flight-price check per candidate (see `destination_recommender_agent`).

Dropped entirely (no replacement found):
- **Airport transfers** (`search_airport_transfers`) — Duffel has no
  point-to-point ground transport product (it does have Duffel Cars, a car
  *rental* product launched after this migration — different shape, not
  wired in).
- **"Best time to book" pricing** (`get_price_insight`) — no Duffel
  equivalent to Amadeus's historical price-quartile API.

Also fixed while touching `guardrails.py`: `FlightValidator.is_acceptable`
parsed an ISO-8601 `duration` string that Amadeus's response included but
Duffel's doesn't — silently making the max-duration guardrail a no-op. Now
computed from segment departure/arrival timestamps instead (provider-agnostic,
verified against a synthetic 12-hour offer).

### What's Built

A **multi-agent Google ADK project** structured to plan end-to-end trips within budget, with guardrails and timeouts to prevent suboptimal decisions and infinite loops, and certainty-aware option presentation so undecided users get real choices instead of one silently-picked answer.

#### Architecture

- **Root Coordinator** (`travel_planner/agent.py`)
  - Orchestrates trip planning via conversation
  - Asks if user has a destination or needs recommendations (two workflows)
  - Gathers required details: origin, destination, dates, number of travelers, total budget
  - Asks for optional preferences: interests, travel class
  - Reads user certainty at each decision point and tells specialists whether
    to auto-select or return multiple options (see Certainty & Budget Buffer below)
  - Calls `trip_planning_pipeline` — a graph-based `Workflow` (see below) — once
    it has everything, which runs booking, itinerary, and packing planning
    **concurrently** rather than one after another, then presents the merged,
    reconciled result
  - Calls `export_trip_plan_to_excel` when (and only when) the user asks to
    save/export/download the plan

- **Destination Recommender** (`travel_planner/sub_agents/destination_recommender/agent.py`)
  - For users who don't know where to go
  - Brainstorms 5-8 candidates from the model's own travel knowledge (origin,
    season, budget tier, interests), then validates each with `resolve_city`
    and grounds it with a real Duffel indicative flight price
  - Returns 3-5 recommended destinations with reasoning
  - Only invoked if user chooses "I need destination ideas" path

- **Booking Specialist** (`travel_planner/sub_agents/booking_agent/agent.py`)
  - Resolves city names to IATA codes via Duffel Places Suggestions
  - Searches live flight offers via Duffel's Offer Requests API
  - **Applies flight guardrails:** strongly prefers non-stop, rejects 3+ stops, max 20h total duration
  - Recommends the best flight within transportation budget
  - Returns cost breakdown for the specialist

- **Itinerary Specialist** (`travel_planner/sub_agents/itinerary_planner/agent.py`)
  - Searches real points of interest near destination via Google Places API (New)
  - Builds day-by-day plan (morning, afternoon, evening blocks)
  - Grounds recommendations in real place names
  - Stays within activities budget (total budget minus transportation)
  - Returns itinerary with running cost estimates

- **Packing Specialist** (`travel_planner/sub_agents/packing_agent/agent.py`)
  - Gets a real forecast or historical typical-conditions estimate via Open-Meteo
  - Builds a weather- and interest-appropriate packing list
  - Runs independently in the same parallel fan-out as booking/itinerary (doesn't touch budget)

#### Tools (Duffel + Google Places + Open-Meteo API wrappers)

- `resolve_city(city_name)` → IATA codes, coordinates, country code (Duffel Places Suggestions)
- `search_flights(origin_iata, destination_iata, dates, ...)` → live offers with prices, stops, times (Duffel Offer Requests)
- `search_points_of_interest(latitude, longitude, radius, category)` → real attractions, restaurants, shops (Google Places Nearby Search)
- `get_city_info(city_name_or_iata)` → timezone, country, location details (Duffel Places Suggestions)
- `get_weather_outlook(latitude, longitude, start_date, end_date)` → real forecast or historical average (Open-Meteo, no API key)
- `remember_trip_preferences(...)` / `remember_completed_trip(...)` → cross-session user memory (see below)
- `export_trip_plan_to_excel(...)` → downloadable `.xlsx` via ADK's artifact system

#### Guardrails, Timeouts & Certainty-Aware Options

- **Guardrails** (`travel_planner/guardrails.py`): Flight validator prefers non-stop, rejects 3+ stops and 20+ hour flights
- **Timeouts** (`travel_planner/timeouts.py`) — **framework-enforced, not just prompted**:
  - `AGENT_TIMEOUT` (60s): passed as the `timeout` kwarg directly on each node
    (`booking_step`, `itinerary_step`, `plan_merge_step`) in the Workflow graph —
    a node that overruns is cancelled and raises `NodeTimeoutError` automatically.
  - `COORDINATOR_TIMEOUT` (180s): set as the outer `Workflow`'s own `timeout`.
  - This replaced an earlier version that only *told* agents about time limits
    in their instructions — real enforcement only became possible once the
    project moved onto ADK's native `Workflow`/node primitives (each `BaseNode`
    has a real `timeout`/`retry_config` field the framework itself honors).
- **Certainty & Budget Buffer** (`travel_planner/guardrails.py`) — **NEW**
  - `CertaintyLevel` (CERTAIN / FLEXIBLE / UNCERTAIN): the coordinator reads how
    locked-in the user sounds about a decision (destination, flight, itinerary
    theme) from the conversation itself — no sentiment model, just an LLM
    judgment call guided by `CERTAINTY_GUIDANCE` text embedded in every
    agent's instructions.
  - When UNCERTAIN, specialists don't silently auto-pick: `booking_agent`
    returns up to 3 flight options with trade-offs, `destination_recommender_agent`
    returns 3 meaningfully different destinations (not near-duplicates), and
    `itinerary_planner_agent` can build two themed variants — coordinator
    presents these and waits for the user to choose before locking in budget math.
  - `BudgetBuffer` (default 15%): options priced up to 15% over the stated
    budget aren't discarded outright — they're shown as a tagged "stretch"
    option when the user is flexible/uncertain, never forced on a
    budget-firm user, and never used to inflate the coordinator's actual
    cost-vs-budget accounting (that always uses the real chosen price).
  - `FlightValidator.top_options()` / `DestinationValidator.top_options()`:
    pure-Python helpers that rank + tag options by budget status; available
    for programmatic use, though today the agents apply this logic via their
    instructions (see "What's Not Yet Done" below).

#### Project Files

```
travel_planner/
├── agent.py                              (root coordinator)
├── schemas.py                            (TripPlanningRequest — the Workflow's input_schema)
├── guardrails.py                         (flight/destination/itinerary preferences, certainty, budget buffer)
├── timeouts.py                           (AGENT_TIMEOUT / COORDINATOR_TIMEOUT constants)
├── sub_agents/
│   ├── booking_agent/agent.py            (flight search)
│   ├── itinerary_planner/agent.py        (day-by-day activities)
│   ├── packing_agent/agent.py            (weather-aware packing list)
│   ├── destination_recommender/agent.py  (destination brainstorming + price validation)
│   ├── plan_merger/agent.py              (combines all three, reconciles budget)
│   └── parallel_pipeline.py              (the Workflow graph — see write-up above)
└── tools/
    ├── duffel_client.py                  (shared Duffel REST client)
    ├── places_client.py                  (shared Google Places REST client)
    ├── weather_tools.py                  (get_weather_outlook — Open-Meteo, no key needed)
    ├── memory_tools.py                   (remember_trip_preferences, remember_completed_trip)
    ├── excel_export_tools.py             (export_trip_plan_to_excel)
    ├── location_tools.py                 (resolve_city)
    ├── flight_tools.py                   (search_flights)
    ├── poi_tools.py                      (search_points_of_interest)
    └── destination_tools.py              (get_city_info)

.env.example                              (copy to .env, fill in credentials)
requirements.txt                          (google-adk>=2.7.0, requests, openpyxl)
.gitignore                                (venv, __pycache__, .env)
README.md                                 (setup, run, limitations)
PROGRESS.md                               (this file)
SEQUENCE_DIAGRAM.md                       (architecture diagram with PNG)
```

### Verification ✓

- ✓ All Python imports resolve correctly
- ✓ Agent and Workflow definitions are valid ADK schemas
- ✓ `Workflow` graph builds correctly: `START → seed_trip_request → (booking_step ‖ itinerary_step) → booking_itinerary_join → plan_merge_step` (printed and inspected directly)
- ✓ `booking_step`/`itinerary_step`/`plan_merge_step` are confirmed clones of the original agents, not the same objects (no parent-agent conflicts)
- ✓ `await root_agent.canonical_tools()` resolves `trip_planning_pipeline` into a real `NodeTool` with the expected 13-field declaration, without needing to import `NodeTool` ourselves
- ✓ `adk web` boots successfully
- ✓ `/list-apps` API endpoint returns all five agents:
  - `travel_planner` (root coordinator)
  - `travel_planner.sub_agents.destination_recommender`
  - `travel_planner.sub_agents.booking_agent`
  - `travel_planner.sub_agents.itinerary_planner`
  - `travel_planner.sub_agents.plan_merger`
- ✗ **Not verified**: a live, model-driven run of the full pipeline (no API key configured in this environment) — see the caveat at the end of the Workflow write-up above

### On ADK's graph/node Workflow system (what we actually run on)

The pipeline is built on ADK 2.x's real graph orchestration engine at
`google.adk.workflow` (`Workflow`, `JoinNode`, the `node()` wrapper) — the
SDK's own docstrings say this replaces the older `ParallelAgent`/
`SequentialAgent` "workflow agent" classes, which we started with and then
migrated off of. `sub_agents/parallel_pipeline.py` has the full design
writeup; short version:

- `trip_planning_pipeline` is a `Workflow` whose graph fans out from a single
  `seed_trip_request` step into `booking_step` and `itinerary_step` running
  concurrently, joins on `booking_itinerary_join` (a `JoinNode`, which blocks
  until both branches finish), then feeds `plan_merge_step`.
- It's exposed to the coordinator with **no explicit tool wrapper at all**:
  `LlmAgent.tools` accepts a `Workflow` directly, and ADK's own
  `_convert_tool_union_to_tools` (in the public `llm_agent.py`, not a private
  module) auto-wraps any `BaseNode` that isn't itself a `BaseAgent` via
  `NodeTool`. Verified directly: `await root_agent.canonical_tools()` returns
  a real `NodeTool` instance we never constructed ourselves, with a function
  declaration exposing all 13 `TripPlanningRequest` fields as typed
  parameters (see `schemas.py`) — a real reliability upgrade over the earlier
  design's single freeform "request" string.
- `booking_agent`/`itinerary_planner_agent` are reused as-is; `node()` (via
  `build_node()`) *clones* an `LlmAgent` when wrapping it rather than
  mutating it, confirmed directly (`booking_node is booking_agent` → `False`;
  the original's `mode`/`parent_agent` stay untouched) — so there was no risk
  in reusing the same agent objects that used to be called individually.
- **The one non-obvious fix this design needed**: reading `BaseAgent._run_impl`
  directly showed that an agent used as a graph node *ignores its own
  `node_input` argument* and instead just reads whatever's already in the
  session — so naively fanning `node_input` out from `START` to
  `booking_step`/`itinerary_step` would silently discard the coordinator's
  request. `seed_trip_request` is a small `FunctionNode` that runs first,
  receives the validated `TripPlanningRequest` (a function parameter
  literally named `node_input` is bound to it directly — confirmed by reading
  `FunctionNode._bind_parameters`), and returns it as a `types.Content` —
  one of `FunctionNode`'s recognized pass-through output types — which
  appends it to the session as a real conversational turn for the two
  branches to read.

**What's verified vs. assumed**: graph construction, the auto-`NodeTool`
wrapping, the clone-not-mutate behavior, and the parameter-binding mechanics
above were all confirmed by direct inspection of the installed `google-adk`
2.7.0 source and by constructing the real objects in a Python shell — not
guessed. What's *not* verified is a full live run: no `GOOGLE_API_KEY` is
configured in this environment, so the actual model-driven conversation
(coordinator → tool call → parallel branches → merge → final answer) has
never executed end-to-end. If `seed_trip_request`'s injected content doesn't
reach `booking_step`/`itinerary_step` the way the source suggests it should,
that's the first place to look when testing with real credentials.

### What's Not Yet Done

1. **Credentials** — User must sign up for:
   - Free Gemini API key (Google AI Studio)
   - Free Duffel test-mode access token (duffel.com)
   - Google Places API (New) key (Google Cloud project with billing enabled)

2. **E2E testing** — Has not been run conversationally yet; no example trip plans generated

3. **Guardrail enforcement in agents** — Guardrails (including the certainty/buffer logic) are defined in `guardrails.py` and referenced in agent instructions, but the Python helper methods (`FlightValidator.top_options()`, etc.) aren't called as tools by the agents — the LLM applies the same logic by reasoning over the instruction text. This is by design (agents should reason about constraints) but could be hardened by exposing these as callable tools if stricter enforcement is needed.

4. **Live validation of the Workflow's data flow** — see the caveat at the end of the "On ADK's graph/node Workflow system" section above; this is the highest-value thing to test first once credentials are configured.

5. **Lodging** — Out of scope; budget covers transportation + activities/food only

6. **Real bookings** — Uses Duffel test-mode sandbox (realistic-pattern data, no actual purchases)

7. **No airport transfers or "best time to book"** — dropped in the Amadeus migration above; no equivalent found on Duffel/Google Places

### Next Steps

1. Fill in `travel_planner/.env` with credentials
2. Run `adk web` and test a sample trip in the UI
3. Commit to GitHub (currently all changes are uncommitted locally)
