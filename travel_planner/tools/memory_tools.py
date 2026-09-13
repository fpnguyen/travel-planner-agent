"""Cross-session user memory, built on ADK's user-scoped session state.

Any state key prefixed "user:" is persisted separately from a single
conversation and merged into every future session for that same user_id —
confirmed directly against the SDK: SqliteSessionService (what `adk web` uses
by default for local persistence) keeps a dedicated user-state table and
re-merges it whenever a new session is created for that user, surviving both
new sessions and full process restarts as long as the same session.db file
is used.

A tool function gets access to that state by declaring a `tool_context:
ToolContext` parameter — ADK auto-injects it and hides it from the schema the
LLM sees, so the model never has to pass it as an argument.
"""

from google.adk.tools.tool_context import ToolContext


def remember_trip_preferences(
    tool_context: ToolContext,
    interests: str = "",
    preferred_travel_class: str = "",
    flight_stop_tolerance: str = "",
) -> dict:
    """Persists durable travel preferences for this user, across sessions.

    Call this once a preference is confirmed as durable (the user's general
    taste, not just a detail of the current trip) — e.g. after presenting a
    final plan, or whenever the user states something like "I always want
    nonstop flights." Only pass the fields that actually changed; omitted
    fields (left as "") are left untouched, so this never erases a
    previously-remembered preference by accident.

    Args:
        interests: Free-text travel interests, e.g. "food, history, hiking".
        preferred_travel_class: One of ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST.
        flight_stop_tolerance: e.g. "nonstop only", "up to 1 stop is fine".

    Returns:
        {"status": "success", "saved": {...fields actually written...}}
    """
    saved = {}
    if interests:
        tool_context.state["user:interests"] = interests
        saved["interests"] = interests
    if preferred_travel_class:
        tool_context.state["user:preferred_travel_class"] = preferred_travel_class
        saved["preferred_travel_class"] = preferred_travel_class
    if flight_stop_tolerance:
        tool_context.state["user:flight_stop_tolerance"] = flight_stop_tolerance
        saved["flight_stop_tolerance"] = flight_stop_tolerance

    return {"status": "success", "saved": saved}


def remember_completed_trip(
    tool_context: ToolContext,
    destination: str,
    start_date: str,
    end_date: str,
    total_cost: float,
    currency: str = "USD",
) -> dict:
    """Appends a finalized trip plan to this user's cross-session trip history.

    Call this once after presenting the final plan to the user (not for
    draft/rejected options). This is a record that a plan was *presented*,
    not proof the user actually booked or traveled — say so if you reference
    it later (e.g. "last time you planned a trip to Lisbon" rather than
    "your trip to Lisbon").

    Args:
        destination: City name, e.g. "Lisbon".
        start_date: Trip start date, YYYY-MM-DD.
        end_date: Trip end date, YYYY-MM-DD.
        total_cost: The final plan's total cost.
        currency: 3-letter currency code.

    Returns:
        {"status": "success", "trip_history_count": <int>}
    """
    history = tool_context.state.get("user:trip_history", [])
    history = list(history) + [
        {
            "destination": destination,
            "start_date": start_date,
            "end_date": end_date,
            "total_cost": total_cost,
            "currency": currency,
        }
    ]
    tool_context.state["user:trip_history"] = history

    return {"status": "success", "trip_history_count": len(history)}
