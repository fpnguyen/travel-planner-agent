import requests

from .duffel_client import duffel_request


def get_city_info(city_name_or_iata: str) -> dict:
    """Gets identifying details about a city for travel planning.

    Args:
        city_name_or_iata: A city name (e.g. "Lisbon") or 3-letter IATA city
            code (e.g. "LIS").

    Returns:
        On success: {"status": "success", "name", "iata_code", "country_code",
        "timezone", "latitude", "longitude"}.
        On error: {"status": "error", "error_message"}.
    """
    try:
        response = duffel_request(
            "GET", "/places/suggestions", params={"query": city_name_or_iata}
        )
    except requests.HTTPError as error:
        return {"status": "error", "error_message": str(error)}

    places = response.get("data", [])
    cities = [p for p in places if p.get("type") == "city"]
    if not cities:
        return {"status": "error", "error_message": f"City '{city_name_or_iata}' not found."}

    city = cities[0]
    return {
        "status": "success",
        "name": city.get("name"),
        "iata_code": city.get("iata_city_code") or city.get("iata_code"),
        "country_code": city.get("iata_country_code"),
        "timezone": city.get("time_zone"),
        "latitude": city.get("latitude"),
        "longitude": city.get("longitude"),
    }
