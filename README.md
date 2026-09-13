# travel-planner-agent

A multi-agent trip planner built on [Google's Agent Development Kit (ADK)](https://google.github.io/adk-docs/).
Tell it where you're going (or ask it to suggest somewhere), when, and your
budget — it asks for anything it's missing, then finds real flights via
Duffel, builds a day-by-day itinerary grounded in real points of interest via
Google Places, and puts together a weather-aware packing list via
Open-Meteo, all within budget. It remembers your preferences across separate
conversations, and can export the finished plan to an Excel file on request.

> **Provider note:** this project originally ran on Amadeus's self-service
> API, which Amadeus fully decommissioned on 2026-07-17 (announced February
> 2026, new registrations paused that spring). It's since been migrated to
> Duffel (flights) and Google Places (points of interest) — see "Notes &
> limitations" below for what changed as a result (dropped airport-transfer
> search and "best time to book" pricing, neither of which have equivalents
> on the new providers).

## How it works

```
travel_planner/                          (root ADK agent package)
├── agent.py                             travel_planner_coordinator (root_agent)
├── schemas.py                           TripPlanningRequest — typed input to the pipeline
├── guardrails.py                        flight/destination/itinerary preferences,
│                                         certainty levels, and budget buffer logic
├── timeouts.py                          AGENT_TIMEOUT / COORDINATOR_TIMEOUT constants
├── sub_agents/
│   ├── destination_recommender/         suggests destinations when the user doesn't know
│   ├── booking_agent/                   flight search
│   ├── itinerary_planner/               day-by-day itinerary using real POIs
│   ├── packing_agent/                   weather-aware packing list
│   ├── plan_merger/                     combines all three into one plan, reconciles budget
│   └── parallel_pipeline.py             the Workflow graph tying it together
└── tools/                               Duffel + Google Places + Open-Meteo API wrappers,
                                          plus memory_tools.py and excel_export_tools.py
```

- **travel_planner_coordinator** (root) — talks to the user, asks for any
  missing trip details, gauges how certain the user sounds at each decision
  point (see "Certainty & options" below), then calls `trip_planning_pipeline`
  once it has everything and relays the result.
- **destination_recommender_agent** — for users who don't have a destination
  yet: brainstorms candidates from its own travel knowledge (given origin,
  dates, budget, interests), then grounds each one with a real Duffel flight
  price check before presenting options — there's no live "destination
  inspiration by budget" API available since Amadeus shut down (see note
  above), so this replaces that with model reasoning + real price validation.
- **trip_planning_pipeline** — a `Workflow` (ADK's graph/node orchestration
  engine, `google.adk.workflow`) that runs `booking_agent`,
  `itinerary_planner_agent`, and `packing_agent` **concurrently** rather than
  one after another, since none of them actually depend on each other's tool
  calls — booking/itinerary only share how the budget is split between them,
  and packing doesn't touch budget at all. A `JoinNode` blocks until all
  three finish, then `plan_merger_agent` combines them (and reconciles
  booking/itinerary's real combined cost against the user's actual total
  budget). See `SEQUENCE_DIAGRAM.md` for the full graph and `PROGRESS.md` for
  the design write-up (why the graph needed a small `seed_trip_request` step,
  how it gets exposed to the coordinator as a tool with zero manual wrapping,
  etc.).
  - **booking_agent** — resolves cities to IATA codes and searches live
    flight offers via Duffel's Offer Requests API, recommending the best
    option(s) within its share of the budget.
  - **itinerary_planner_agent** — searches real points of interest via
    Google Places API (New) Nearby Search near the destination and builds a
    day-by-day plan that fits its share of the budget.
  - **packing_agent** — gets a weather outlook for the destination and dates
    via Open-Meteo (a real forecast if the trip is soon, otherwise a
    typical-conditions estimate averaged from the last 3 years — see
    "Weather-aware packing" below) and builds a packing list from it.

Because it's a standard ADK agent, running `adk web` gives you a chat UI
where the coordinator will ask you directly for anything it needs (dates,
budget, etc.) before it starts searching.

### Certainty & options

Users planning a trip often don't have a firm answer for every question. The
coordinator reads how locked-in the user sounds ("book whatever's cheapest"
vs. "not sure, surprise me") and adjusts behavior accordingly:

- **Certain** → the relevant specialist auto-selects the single best option.
- **Flexible / uncertain** → instead of silently picking for the user, the
  specialist returns a small set of meaningfully different options (e.g. 3
  flights with different price/stop trade-offs, or 3 distinct destination
  vibes) for the user to choose from.

A **budget buffer** (15% by default) means options priced slightly over the
stated budget aren't discarded outright — they're surfaced as a tagged
"stretch" option when the user isn't budget-firm, rather than disappearing
from consideration entirely. Final cost-vs-budget math always uses the real
price of whatever was actually chosen, never the buffered ceiling.

### Cross-session memory

The coordinator remembers a user's interests, preferred travel class, flight
stop tolerance, and past planned trips **across separate conversations**, not
just within one chat. This rides on ADK's built-in `user:`-prefixed session
state: `travel_planner/tools/memory_tools.py` provides `remember_trip_preferences`
and `remember_completed_trip`, called after presenting a final plan, and the
coordinator's own instructions reference `{user:interests?}` etc. directly so
a returning user's profile is just present in the prompt on their next visit.

This works out of the box with `adk web`'s default local storage (a SQLite
file at `travel_planner/.adk/session.db`) — persistence survives both new
sessions and restarting `adk web` entirely, since it's the same user_id
resolving to the same file each time. It only breaks down if you swap to
`--session_service_uri=memory://` (explicitly in-memory) or point multiple
distinct `user_id`s at the same person (e.g. a real multi-user deployment
needs your own auth layer supplying a stable `user_id` — `adk web`'s browser
UI hardcodes a single `user_id="user"`, which is fine for local dev but not
representative of a real multi-tenant setup).

### Weather-aware packing

`packing_agent` uses [Open-Meteo](https://open-meteo.com) — free, no API key
needed for non-commercial use:

- **Trip starting within ~15 days**: uses Open-Meteo's real forecast API.
- **Further out** (the common case — most trips are planned weeks or months
  ahead, and forecasts simply don't exist that far out): averages the same
  calendar dates from the last 3 years via Open-Meteo's historical archive
  API instead, as a "typical conditions" estimate. The agent always says
  which one it used — the historical case is explicitly labeled as an
  estimate to re-check closer to the trip, not presented as a real forecast.

### Excel export

Ask the coordinator to export, save, or download the plan (e.g. "can I get
this as a file?") and it calls `export_trip_plan_to_excel`, which builds a
three-sheet workbook (Overview, Itinerary, Packing List) with `openpyxl` and
attaches it via ADK's artifact system (`tool_context.save_artifact`) — it
shows up as a real downloadable file in the chat UI, not a path on the
server's disk. This is opt-in; the coordinator never exports automatically.

## Setup

1. **Python 3.10+** and a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Credentials** — copy the example env file and fill it in:

   ```bash
   cp travel_planner/.env.example travel_planner/.env
   ```

   - `GOOGLE_API_KEY` — a free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).
   - `DUFFEL_ACCESS_TOKEN` — a free test-mode access token from
     [duffel.com](https://duffel.com) (Dashboard > Developers > Access Tokens,
     with "Developer test mode" on — tokens start with `duffel_test_` and
     return sandbox data only, no real bookings or payment risk).
   - `GOOGLE_PLACES_API_KEY` — an API key from a Google Cloud project with
     "Places API (New)" enabled. Google requires billing to be turned on even
     to use the free monthly quota — see
     [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials).

3. **Run it**:

   ```bash
   adk web
   ```

   Open the URL it prints, select `travel_planner` from the agent dropdown,
   and start chatting, e.g.:

   > *"I want to go from Boston to Lisbon, 6 days starting September 10th,
   > budget $2500 for 1 traveler, I love food and history."*

## Notes & limitations

- **This does not complete real purchases.** Duffel's test-mode token returns
  realistic-pattern search data from a fictional "Duffel Airways" carrier for
  recommendation purposes — it's not live inventory, and actually issuing a
  ticket requires a production Duffel account and payment handling, which is
  out of scope for this project. The agent hands you a concrete-looking
  recommendation; you complete the purchase yourself with a real airline.
- **No airport transfer / ground transportation search.** This project
  originally used Amadeus's Transfer Search API for this; Duffel doesn't have
  a directly equivalent product (it does have a separate car-rental product,
  Duffel Cars, launched after this migration — worth a future look, but not
  wired in here).
- **No "best time to book" pricing.** Amadeus's Flight Price Analysis API
  (historical price quartiles for a route) doesn't have an equivalent on
  Duffel; this feature was removed rather than approximated.
- **Lodging is out of scope.** The itinerary planner budgets for activities
  and food only, not hotels.
- **Packing lists for far-future trips are estimates, not forecasts.** Beyond
  Open-Meteo's ~15-day forecast window, the packing list is based on averaged
  weather from the same calendar dates in the last 3 years — labeled as such,
  but still just an estimate. Re-check closer to the trip.
