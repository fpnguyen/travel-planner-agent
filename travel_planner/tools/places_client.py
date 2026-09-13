"""Shared Google Places API (New) client.

Replaces Amadeus's Points of Interest API, which Amadeus fully decommissioned
on 2026-07-17 (see README for details). Uses the Places API (New) REST
endpoints directly via `requests` — no dedicated SDK needed for the single
Nearby Search endpoint this project uses.

Requires a Google Cloud project with the "Places API (New)" enabled and
billing turned on (Google requires billing even to stay within the free
monthly quota — see README for setup).
"""

import os

import requests

PLACES_API_BASE = "https://places.googleapis.com/v1"


def places_request(path: str, body: dict, field_mask: str) -> dict:
    """Makes an authenticated POST request to the Places API (New) and returns parsed JSON.

    Args:
        path: API path starting with "/", e.g. "/places:searchNearby".
        body: The JSON request body.
        field_mask: Comma-separated field paths to return (required by the API —
            requests without one are rejected).

    Returns:
        The parsed JSON response body.

    Raises:
        RuntimeError: If GOOGLE_PLACES_API_KEY is not set.
        requests.HTTPError: If the API returns a non-2xx response.
    """
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_PLACES_API_KEY is not set. Copy travel_planner/.env.example to "
            "travel_planner/.env and fill it in with an API key from a Google Cloud "
            "project that has the 'Places API (New)' enabled and billing turned on "
            "(see README for setup)."
        )

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }
    response = requests.post(
        f"{PLACES_API_BASE}{path}", headers=headers, json=body, timeout=30
    )
    response.raise_for_status()
    return response.json()
