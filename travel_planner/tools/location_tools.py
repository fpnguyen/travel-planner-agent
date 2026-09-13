import requests

from .duffel_client import duffel_request


def resolve_city(city_name: str) -> dict:
    """Resolves a free-text city name into IATA codes and coordinates.

    Use this first for any city the user mentions (origin, destination) so that
    other tools can be called with proper IATA codes and coordinates instead of
    raw city names.

    Args:
        city_name: A city name as typed by the user, e.g. "Tokyo" or "New York".

    Returns:
        On success: {"status": "success", "name", "iata_city_code",
        "iata_airport_codes" (list), "latitude", "longitude", "country_code"}.
        On failure: {"status": "error", "error_message"}.
    """
    try:
        response = duffel_request(
            "GET", "/places/suggestions", params={"query": city_name}
        )
    except requests.HTTPError as error:
        return {"status": "error", "error_message": str(error)}

    places = response.get("data", [])
    if not places:
        return {"status": "error", "error_message": f"No location found for '{city_name}'."}

    cities = [p for p in places if p.get("type") == "city"]
    airports = [p for p in places if p.get("type") == "airport"]
    primary = cities[0] if cities else places[0]

    return {
        "status": "success",
        "name": primary.get("name"),
        "iata_city_code": primary.get("iata_city_code") or primary.get("iata_code"),
        "iata_airport_codes": [a.get("iata_code") for a in airports] or [primary.get("iata_code")],
        "latitude": primary.get("latitude"),
        "longitude": primary.get("longitude"),
        "country_code": primary.get("iata_country_code"),
    }
