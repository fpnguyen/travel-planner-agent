# Travel Planner Agent — Sequence Diagram

![Sequence diagram showing multi-agent coordination flow](docs/sequence_diagram.png)

## Diagram Source

```mermaid
sequenceDiagram
    participant User
    participant Root as Root Coordinator<br/>(travel_planner_coordinator)
    participant Dest as Destination Recommender<br/>(optional, AgentTool)
    participant Seed as seed_trip_request<br/>(FunctionNode)
    participant Booking as booking_step<br/>(booking_agent as node)
    participant Itinerary as itinerary_step<br/>(itinerary_planner as node)
    participant Join as booking_itinerary_join<br/>(JoinNode)
    participant Merger as plan_merge_step<br/>(plan_merger_agent as node)
    participant Duffel as Duffel API
    participant Places as Google Places API

    User->>Root: Start: origin, dates, budget, travelers, (destination?)
    Note over Root: Check for missing details;<br/>read certainty (flight prefs, interests)

    alt Missing any required field
        Root->>User: Ask for missing info
        User->>Root: Provide missing details
    end

    opt User doesn't know destination
        Root->>Dest: Recommend destinations<br/>(origin, budget, dates, interests)
        Dest->>Dest: Brainstorm 5-8 candidates<br/>from own travel knowledge
        Dest->>Duffel: resolve_city(candidate) for each
        Dest->>Duffel: search_flights(origin, candidate, date)<br/>— real indicative price per candidate
        Duffel-->>Dest: IATA codes + flight prices
        Dest-->>Root: 3-5 destination options,<br/>each grounded with a real price
        Root->>User: Present options
        User->>Root: Picks a destination
    end

    Note over Root: Pre-allocate budget ESTIMATE split<br/>(~40% transport / ~60% activities)

    Root->>Seed: trip_planning_pipeline(TripPlanningRequest)<br/>— a typed tool call, auto-wrapped via NodeTool
    Note over Seed: Converts the structured request into<br/>a real conversational turn (agent nodes<br/>ignore raw node_input, per BaseAgent._run_impl)

    Seed->>Booking: (fan-out)
    Seed->>Itinerary: (fan-out, concurrent)

    par Booking branch
        activate Booking
        Booking->>Duffel: resolve_city(origin), resolve_city(destination)
        Booking->>Duffel: search_flights(...)
        Note over Booking: Apply guardrails: prefer nonstop,<br/>max 2 stops, max 20h duration, budget buffer.<br/>60s hard timeout (framework-enforced)
        Booking->>Booking: output_key="booking_result"
        deactivate Booking
    else Itinerary branch
        activate Itinerary
        Itinerary->>Duffel: resolve_city(destination)
        Itinerary->>Places: search_points_of_interest(...)
        Note over Itinerary: Build day-by-day plan against<br/>ESTIMATED activities budget.<br/>60s hard timeout
        Itinerary->>Itinerary: output_key="itinerary_result"
        deactivate Itinerary
    end

    Booking->>Join: done
    Itinerary->>Join: done
    Note over Join: Blocks until BOTH branches finish

    Join->>Merger: proceed
    activate Merger
    Note over Merger: Reads {booking_result} / {itinerary_result}<br/>from session state; sums real costs,<br/>reconciles vs. stated total budget
    Merger-->>Root: One combined trip plan
    deactivate Merger

    Root->>User: Final trip plan<br/>(cost breakdown + flight + itinerary)
```

## Data Flow Summary

| Stage | Input | Processing | Output |
|-------|-------|-----------|--------|
| **Gathering** | User conversation | Coordinator asks for: origin, destination (or "recommend one"), dates, budget, travelers, interests, travel class; reads certainty per decision point | Trip requirements confirmed |
| **Destination (optional)** | Origin, budget, dates, interests | `destination_recommender_agent`: brainstorm candidates → `resolve_city` → `search_flights` for a real price per candidate | 3-5 destination options, user picks one |
| **Budget split (upfront estimate)** | Total budget | Coordinator pre-allocates ~40% transport / ~60% activities *before* either specialist runs | `TripPlanningRequest` with both estimates |
| **Seed** | `TripPlanningRequest` | `seed_trip_request` (FunctionNode) converts the structured request into a real conversational turn | A session turn both branches can read |
| **Booking + Itinerary (parallel)** | That turn | `Workflow`'s graph runs `booking_step`/`itinerary_step` concurrently — neither sees the other's output | `booking_result`, `itinerary_result` (session state via `output_key`) |
| **Join** | Both branches' completion | `booking_itinerary_join` (`JoinNode`) blocks until both finish | Synchronization only — the actual data travels via session state, not through the join |
| **Merge & reconcile** | `booking_result`, `itinerary_result`, user's real total budget | `plan_merge_step`: sum actual costs, compare vs. real budget, flag over/under | One final trip plan with accurate cost breakdown |

## Key Design Decisions

- **ADK's graph/node `Workflow` engine, not the deprecated workflow-agent classes**: `google.adk.workflow` (`Workflow`, `JoinNode`, `node()`) is what ADK 2.x's own docs point to as the successor to `ParallelAgent`/`SequentialAgent`. The project started on the latter, then migrated once the graph engine's actual data-flow mechanics were verified against the installed SDK source — see `PROGRESS.md` for the full trail (auto-`NodeTool` wrapping, agent cloning, the `seed_trip_request` fix).
- **No explicit tool wrapper needed**: `trip_planning_pipeline` (a `Workflow`) sits directly in the coordinator's `tools=[...]` list. ADK's own tool-resolution code auto-wraps any non-agent `BaseNode` via `NodeTool` — confirmed by calling `canonical_tools()` directly and getting back a real `NodeTool` we never constructed.
- **Typed tool call, not a text blob**: because the `Workflow` declares `input_schema=TripPlanningRequest`, the coordinator calls it with 13 typed, named parameters (dates, budgets, certainty levels, etc.) instead of one freeform message — a real reliability upgrade over the earlier design.
- **Parallel over sequential**: `booking_agent` and `itinerary_planner_agent` don't actually depend on each other's tool calls — only on how the budget is split between them. Running them concurrently roughly halves wall-clock time for that stage, at the cost of budgeting off an *estimate* rather than the exact leftover amount.
- **Reconciliation, not sequencing, fixes the budget dependency**: `plan_merge_step` sums the two specialists' *actual* costs and compares against the *real* total budget after both finish — so a flight that came in pricier than the 40% estimate still gets caught.
- **Real, framework-enforced timeouts**: each specialist node carries a native `timeout=60s`; the whole `Workflow` carries `timeout=180s`. A node that overruns is cancelled by ADK itself (`NodeTimeoutError`), not just discouraged by a prompt.
- **Certainty-aware options** apply per specialist, independently: booking might auto-select one flight while itinerary returns two themed variants (or vice versa) — the coordinator presents whatever came back rather than forcing a single path.
- **Real data grounding, two providers**: flights and destination validation come from Duffel (test-mode sandbox); points of interest come from Google Places. Neither is Amadeus — see `PROGRESS.md` for why (Amadeus fully shut down its self-service API on 2026-07-17). No airport-transfer search or historical price-quartile ("best time to book") feature survived the migration — dropped, not approximated, since neither provider has an equivalent.
