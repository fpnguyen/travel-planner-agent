import requests

from .places_client import places_request

_CATEGORY_MAP = {
    "SIGHTS": ["tourist_attraction", "museum", "park"],
    "NIGHTLIFE": ["night_club", "bar"],
    "RESTAURANT": ["restaurant"],
    "SHOPPING": ["shopping_mall", "store"],
}

_FIELD_MASK = "places.displayName,places.types,places.rating,places.formattedAddress"


def search_points_of_interest(
    latitude: float,
    longitude: float,
    radius_km: int = 5,
    category: str = "",
    max_results: int = 20,
) -> dict:
    """Searches real points of interest near a location to ground itinerary suggestions.

    Uses the Google Places API (New) Nearby Search.

    Args:
        latitude: Latitude of the search center (e.g. from resolve_city).
        longitude: Longitude of the search center.
        radius_km: Search radius in kilometers (max 50).
        category: Optional filter, one of SIGHTS, NIGHTLIFE, RESTAURANT, SHOPPING.
            Leave empty to search a broad default mix.
        max_results: Maximum number of places to return (1-20).

    Returns:
        On success: {"status": "success", "poi_count", "pois": [{"name",
        "category", "rating", "address"}]}.
        On failure: {"status": "error", "error_message"}.
    """
    included_types = _CATEGORY_MAP.get(category.upper()) if category else None
    body = {
        "maxResultCount": max(1, min(max_results, 20)),
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": min(radius_km, 50) * 1000,
            }
        },
        "rankPreference": "POPULARITY",
    }
    if included_types:
        body["includedTypes"] = included_types
    else:
        body["includedTypes"] = ["tourist_attraction", "restaurant", "museum", "park"]

    try:
        response = places_request("/places:searchNearby", body, _FIELD_MASK)
    except requests.HTTPError as error:
        return {"status": "error", "error_message": str(error)}

    places = response.get("places", [])
    pois = [
        {
            "name": place.get("displayName", {}).get("text"),
            "category": (place.get("types") or [None])[0],
            "rating": place.get("rating"),
            "address": place.get("formattedAddress"),
        }
        for place in places
    ]
    return {"status": "success", "poi_count": len(pois), "pois": pois}
