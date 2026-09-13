"""Guardrails and constraints for agent decision-making.

Prevents agents from making suboptimal choices (e.g., 5-stop, 24-hour
flights) and enforces business logic (e.g., non-stop preferred, budget hard
limits).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CertaintyLevel(Enum):
    """How locked-in the user sounds about a given decision point.

    This isn't computed by a sentiment model — the LLM agent reads it off the
    conversation itself (phrases like "just book whatever's cheapest" vs. "I'm
    not sure, surprise me" vs. no signal either way) and picks the matching
    level before deciding whether to auto-select or present options. See the
    CERTAINTY_GUIDANCE text below, which agent instructions embed directly.
    """

    CERTAIN = "certain"  # User gave a specific, decisive preference
    FLEXIBLE = "flexible"  # User has a lean but isn't fussed either way
    UNCERTAIN = "uncertain"  # User has no strong opinion / said so explicitly


CERTAINTY_GUIDANCE = """
Before finalizing a pick at any decision point (flight, destination, itinerary
theme), judge how certain the user sounds:
- CERTAIN: they named a specific requirement ("nonstop only", "cheapest
  possible", "Lisbon, nowhere else"). Auto-select the single best match and
  move on — asking again would be annoying.
- FLEXIBLE: they gave a lean but signaled openness ("I'd prefer somewhere
  warm, but I'm easy"). Auto-select your best pick, but mention the runner-up
  briefly in case they'd rather switch.
- UNCERTAIN: they gave no strong preference, said "not sure" / "you decide" /
  "surprise me", or this is their first time being asked. Do NOT silently lock
  in a single choice — present 2-3 real options with the trade-offs between
  them (price, time, vibe) and let the user pick before you proceed.
When in doubt, treat it as UNCERTAIN. A wasted follow-up question costs one
turn; a wrong unilateral choice costs the whole plan being redone.
"""


@dataclass
class BudgetBuffer:
    """How much slack to allow around a stated budget before rejecting an option.

    Users rarely know their budget to the dollar, and a slightly pricier
    option is sometimes worth surfacing rather than silently discarding. This
    buffer does not raise the budget used in downstream math (the coordinator
    still subtracts the *actual* chosen price) — it only controls which
    options are shown to the user at all.
    """

    buffer_percent: float = 15.0  # Options up to budget * (1 + buffer) are shown, tagged as "stretch"
    options_to_present_when_uncertain: int = 3

    def classify(self, price: float, budget: float) -> str:
        """Classify a price as "within_budget", "stretch", or "over_buffer"."""
        if budget <= 0:
            return "within_budget"
        if price <= budget:
            return "within_budget"
        if price <= budget * (1 + self.buffer_percent / 100):
            return "stretch"
        return "over_buffer"


DEFAULT_BUDGET_BUFFER = BudgetBuffer()


@dataclass
class FlightPreferences:
    """Constraints for flight selection."""

    prefer_nonstop: bool = True
    max_stops: int = 2
    max_total_duration_hours: int = 20
    prefer_arrival_window: tuple[int, int] = (6, 23)  # Prefer arriving 6am-11pm
    skip_if_price_exceeds_budget_by_percent: int = 10  # Reject if >10% over budget


@dataclass
class TransferPreferences:
    """Constraints for airport transfer selection."""

    max_transfer_duration_minutes: int = 90
    prefer_direct: bool = True  # Prefer airport→hotel direct, not via multiple stops


@dataclass
class ItineraryPreferences:
    """Constraints for day-by-day itinerary building."""

    max_activities_per_day: int = 4
    min_rest_time_hours: int = 2  # At least 2 hours between major activities
    exclude_categories: list[str] = None  # e.g. ["NIGHTLIFE"] if family trip
    max_travel_time_between_pois_minutes: int = 45
    prefer_walkable_distance: bool = True


@dataclass
class DestinationPreferences:
    """Constraints for destination recommendations."""

    exclude_countries: list[str] = None  # e.g. ["North Korea"] based on passport
    max_flight_duration_hours: int = 15
    climate_preference: str = ""  # "warm", "cold", "mild", etc.
    require_english_friendly: bool = False  # Skip places with language barrier
    visa_requirement_max_days: int = 30  # Skip if visa takes >30 days


class FlightValidator:
    """Validates flight offers against constraints."""

    def __init__(self, preferences: FlightPreferences):
        self.pref = preferences

    def is_acceptable(self, flight_offer: dict) -> tuple[bool, str]:
        """
        Validate a flight offer.

        Args:
            flight_offer: flight offer dict (from search_flights)

        Returns:
            (is_valid, reason_if_invalid)
        """
        price = float(flight_offer.get("total_price", 0))
        itineraries = flight_offer.get("itineraries", [])

        if not itineraries:
            return False, "No itineraries in offer"

        for itinerary in itineraries:
            segments = itinerary.get("segments", [])

            # Check stop count
            stops = len(segments) - 1
            if self.pref.prefer_nonstop and stops > 0:
                pass  # Soft preference, not hard reject
            if stops > self.pref.max_stops:
                return False, f"Too many stops: {stops} > {self.pref.max_stops}"

            # Check total duration from the first segment's departure to the
            # last segment's arrival (search_flights doesn't return a
            # pre-computed itinerary duration, so derive it from timestamps).
            if segments:
                try:
                    departs = datetime.fromisoformat(segments[0]["departure_time"])
                    arrives = datetime.fromisoformat(segments[-1]["arrival_time"])
                    hours = (arrives - departs).total_seconds() / 3600
                    if hours > self.pref.max_total_duration_hours:
                        return False, (
                            f"Flight too long: {hours:.1f}h > "
                            f"{self.pref.max_total_duration_hours}h"
                        )
                except (KeyError, TypeError, ValueError):
                    pass  # Missing/malformed timestamps — skip the duration check

        return True, ""

    def rank_offers(self, flight_offers: list[dict]) -> list[dict]:
        """
        Rank flight offers by preference (non-stop first, then price).

        Args:
            flight_offers: List of flight offer dicts (from search_flights)

        Returns:
            Sorted list (best first)
        """
        valid_offers = [o for o in flight_offers if self.is_acceptable(o)[0]]

        def sort_key(offer):
            itinerary = offer.get("itineraries", [{}])[0]
            stops = len(itinerary.get("segments", [])) - 1
            price = float(offer.get("total_price", float("inf")))

            # Prefer: (nonstop=0, then price)
            return (stops, price)

        return sorted(valid_offers, key=sort_key)

    def top_options(
        self,
        flight_offers: list[dict],
        budget: float,
        certainty: CertaintyLevel,
        buffer: BudgetBuffer = None,
    ) -> dict:
        """
        Select what to surface to the user, shaped by how certain they are.

        Unlike rank_offers (which just sorts), this decides *how many* offers
        to hand back and whether any "stretch" (over-budget-but-within-buffer)
        options belong in the mix, per CertaintyLevel semantics.

        Args:
            flight_offers: List of flight offer dicts (from search_flights)
            budget: The user's stated transportation budget
            certainty: How locked-in the user sounded (see CERTAINTY_GUIDANCE)
            buffer: BudgetBuffer to use; defaults to BudgetBuffer()

        Returns:
            {"mode": "auto_select" | "present_options", "recommended": offer,
            "alternatives": [offer, ...]} where "alternatives" is empty in
            auto_select mode and up to buffer.options_to_present_when_uncertain
            items (each tagged with a "budget_status" key) in present_options
            mode.
        """
        buffer = buffer or BudgetBuffer()
        ranked = self.rank_offers(flight_offers)

        for offer in ranked:
            offer["budget_status"] = buffer.classify(
                float(offer.get("total_price", float("inf"))), budget
            )

        in_range = [o for o in ranked if o["budget_status"] != "over_buffer"]
        candidates = in_range or ranked  # fall back to cheapest-available if nothing fits even the buffer

        if certainty == CertaintyLevel.UNCERTAIN:
            top_n = candidates[: buffer.options_to_present_when_uncertain]
            return {
                "mode": "present_options",
                "recommended": top_n[0] if top_n else None,
                "alternatives": top_n[1:],
            }

        return {
            "mode": "auto_select",
            "recommended": candidates[0] if candidates else None,
            "alternatives": candidates[1:2] if certainty == CertaintyLevel.FLEXIBLE else [],
        }


class DestinationValidator:
    """Validates destination recommendations against constraints."""

    def __init__(self, preferences: DestinationPreferences):
        self.pref = preferences

    def is_acceptable(self, destination: dict) -> tuple[bool, str]:
        """
        Validate a destination recommendation.

        Args:
            destination: Destination dict with name, country_code, flight_duration_hours

        Returns:
            (is_valid, reason_if_invalid)
        """
        country = destination.get("country_code", "")

        if self.pref.exclude_countries and country in self.pref.exclude_countries:
            return False, f"Country {country} is excluded"

        flight_hours = destination.get("flight_duration_hours", 0)
        if flight_hours > self.pref.max_flight_duration_hours:
            return False, (
                f"Flight too long: {flight_hours}h > "
                f"{self.pref.max_flight_duration_hours}h"
            )

        return True, ""

    def rank_destinations(self, destinations: list[dict]) -> list[dict]:
        """
        Rank destinations by value (price per day, interest match).

        Args:
            destinations: List of destination dicts with estimated_price, flight_duration

        Returns:
            Sorted list (best first)
        """
        valid = [d for d in destinations if self.is_acceptable(d)[0]]

        def sort_key(dest):
            est_price = dest.get("estimated_price", float("inf"))
            flight_hours = dest.get("flight_duration_hours", 20)
            interest_match = dest.get("interest_match_score", 0.5)

            # Prefer: high interest + low price + short flight
            return (
                -interest_match,  # Higher is better
                est_price,  # Lower is better
                flight_hours,  # Shorter is better
            )

        return sorted(valid, key=sort_key)

    def top_options(
        self,
        destinations: list[dict],
        budget: float,
        certainty: CertaintyLevel,
        buffer: BudgetBuffer = None,
    ) -> dict:
        """
        Select what to surface to the user, shaped by how certain they are.

        Destination choice is inherently the highest-uncertainty decision in
        the whole flow (by definition, this agent only runs when the user
        didn't already know where to go) — so this defaults toward presenting
        options unless the user gave a clear steer (e.g. "somewhere warm and
        cheap, you pick the exact city").

        Args:
            destinations: List of destination dicts (see rank_destinations)
            budget: The user's stated total trip budget
            certainty: How locked-in the user sounded about destination type
            buffer: BudgetBuffer to use; defaults to BudgetBuffer()

        Returns:
            Same shape as FlightValidator.top_options: {"mode", "recommended",
            "alternatives"}.
        """
        buffer = buffer or BudgetBuffer()
        ranked = self.rank_destinations(destinations)

        for dest in ranked:
            dest["budget_status"] = buffer.classify(
                dest.get("estimated_price", float("inf")), budget
            )

        in_range = [d for d in ranked if d["budget_status"] != "over_buffer"]
        candidates = in_range or ranked

        if certainty != CertaintyLevel.CERTAIN:
            top_n = candidates[: buffer.options_to_present_when_uncertain]
            return {
                "mode": "present_options",
                "recommended": top_n[0] if top_n else None,
                "alternatives": top_n[1:],
            }

        return {
            "mode": "auto_select",
            "recommended": candidates[0] if candidates else None,
            "alternatives": [],
        }
