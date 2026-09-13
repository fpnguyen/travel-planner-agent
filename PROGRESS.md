# Travel Planner Agent — Progress Summary

## Status: Complete scaffold, ready for testing

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
    it has everything, which runs booking and itinerary planning **concurrently**
    rather than one after another, then presents the merged, reconciled result

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

#### Tools (Duffel + Google Places API wrappers)

- `resolve_city(city_name)` → IATA codes, coordinates, country code (Duffel Places Suggestions)
- `search_flights(origin_iata, destination_iata, dates, ...)` → live offers with prices, stops, times (Duffel Offer Requests)
- `search_points_of_interest(latitude, longitude, radius, category)` → real attractions, restaurants, shops (Google Places Nearby Search)
- `get_city_info(city_name_or_iata)` → timezone, country, location details (Duffel Places Suggestions)

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
│   ├── destination_recommender/agent.py  (destination brainstorming + price validation)
│   ├── plan_merger/agent.py              (reconciles booking + itinerary into one plan)
│   └── parallel_pipeline.py              (the Workflow graph — see write-up above)
└── tools/
    ├── duffel_client.py                  (shared Duffel REST client)
    ├── places_client.py                  (shared Google Places REST client)
    ├── location_tools.py                 (resolve_city)
    ├── flight_tools.py                   (search_flights)
    ├── poi_tools.py                      (search_points_of_interest)
    └── destination_tools.py              (get_city_info)

.env.example                              (copy to .env, fill in credentials)
requirements.txt                          (google-adk>=2.7.0, requests)
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
