"""Structured schemas shared across the trip-planning Workflow graph."""

from typing import Literal

from pydantic import BaseModel, Field


class TripPlanningRequest(BaseModel):
    """Trip details the coordinator has already gathered from the user.

    This is the input_schema for trip_planning_pipeline (see
    sub_agents/parallel_pipeline.py). Because it's a Pydantic model, ADK
    exposes it to the coordinator as typed function-call parameters rather
    than a freeform text blob — the LLM fills these fields directly.
    """

    origin: str = Field(description="City the traveler is departing from, e.g. 'Boston'.")
    destination: str = Field(description="City the traveler is going to, e.g. 'Lisbon'.")
    start_date: str = Field(description="Trip start date, YYYY-MM-DD.")
    end_date: str = Field(description="Trip end date, YYYY-MM-DD.")
    num_travelers: int = Field(description="Number of travelers.")
    total_budget: float = Field(description="The user's total trip budget.")
    currency: str = Field(default="USD", description="3-letter currency code.")
    transportation_budget_estimate: float = Field(
        description=(
            "Pre-allocated share of total_budget for flights "
            "(coordinator's estimate, e.g. ~40% of total_budget)."
        )
    )
    activities_budget_estimate: float = Field(
        description=(
            "Pre-allocated share of total_budget for itinerary activities/food "
            "(coordinator's estimate, e.g. ~60% of total_budget)."
        )
    )
    travel_class: str = Field(
        default="ECONOMY",
        description="ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST.",
    )
    interests: str = Field(
        default="", description="Traveler interests/preferences, e.g. 'food, history'."
    )
    flight_certainty: Literal["certain", "flexible", "uncertain"] = Field(
        description="How locked-in the user sounds about flight preferences."
    )
    itinerary_certainty: Literal["certain", "flexible", "uncertain"] = Field(
        description="How locked-in the user sounds about interests/pacing."
    )
