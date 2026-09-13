"""Shared Duffel API client.

Duffel (https://duffel.com) replaces Amadeus's self-service API, which
Amadeus fully decommissioned on 2026-07-17 (see README for details). Duffel's
official Python client is unmaintained (duffelhq/duffel-api-python — the
maintainers explicitly stopped supporting it for lack of adoption), so this
talks to Duffel's REST API directly via `requests` rather than depending on
an abandoned package.

Uses a free "test mode" access token by default (starts with `duffel_test_`),
which returns realistic but non-bookable data from a fake "Duffel Airways"
carrier — analogous to Amadeus's old sandbox environment.
"""

import os

import requests

DUFFEL_API_BASE = "https://api.duffel.com"
DUFFEL_API_VERSION = "v2"


def duffel_request(method: str, path: str, **kwargs) -> dict:
    """Makes an authenticated request to the Duffel API and returns parsed JSON.

    Args:
        method: HTTP method, e.g. "GET" or "POST".
        path: API path starting with "/", e.g. "/air/offer_requests".
        **kwargs: Passed through to requests.request (params, json, etc.).

    Returns:
        The parsed JSON response body.

    Raises:
        RuntimeError: If DUFFEL_ACCESS_TOKEN is not set.
        requests.HTTPError: If the API returns a non-2xx response.
    """
    access_token = os.getenv("DUFFEL_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError(
            "DUFFEL_ACCESS_TOKEN is not set. Copy travel_planner/.env.example to "
            "travel_planner/.env and fill it in with a free test-mode access token "
            "from https://duffel.com (Dashboard > Developers > Access Tokens, with "
            "'Developer test mode' enabled — tokens start with duffel_test_)."
        )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Duffel-Version": DUFFEL_API_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = requests.request(
        method, f"{DUFFEL_API_BASE}{path}", headers=headers, timeout=30, **kwargs
    )
    response.raise_for_status()
    return response.json()
