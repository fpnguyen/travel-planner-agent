import requests

from .duffel_client import duffel_request

_CABIN_CLASS_MAP = {
    "ECONOMY": "economy",
    "PREMIUM_ECONOMY": "premium_economy",
    "BUSINESS": "business",
    "FIRST": "first",
}


def search_flights(
    origin_iata: str,
    destination_iata: str,
    departure_date: str,
    return_date: str = "",
    adults: int = 1,
    travel_class: str = "ECONOMY",
    max_results: int = 5,
) -> dict:
    """Searches live flight offers via the Duffel Offer Requests API.

    Args:
        origin_iata: 3-letter IATA airport/city code of the departure location, e.g. "JFK".
        destination_iata: 3-letter IATA airport/city code of the destination, e.g. "NRT".
        departure_date: Departure date in YYYY-MM-DD format.
        return_date: Optional return date in YYYY-MM-DD format for round trips. Leave
            empty for a one-way search.
        adults: Number of adult travelers.
        travel_class: One of ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST.
        max_results: Maximum number of offers to return.

    Returns:
        On success: {"status": "success", "offer_count", "offers": [{"offer_id",
        "total_price", "currency", "itineraries": [{"stops",
        "segments": [{"carrier", "flight_number", "departure_airport",
        "departure_time", "arrival_airport", "arrival_time"}]}]}]}.
        On failure: {"status": "error", "error_message"}.
    """
    slices = [{"origin": origin_iata, "destination": destination_iata, "departure_date": departure_date}]
    if return_date:
        slices.append(
            {"origin": destination_iata, "destination": origin_iata, "departure_date": return_date}
        )

    body = {
        "data": {
            "slices": slices,
            "passengers": [{"type": "adult"} for _ in range(max(adults, 1))],
            "cabin_class": _CABIN_CLASS_MAP.get(travel_class.upper(), "economy"),
        }
    }

    try:
        response = duffel_request("POST", "/air/offer_requests", json=body)
    except requests.HTTPError as error:
        return {"status": "error", "error_message": str(error)}

    raw_offers = response.get("data", {}).get("offers", [])

    offers = []
    for offer in raw_offers:
        itineraries_summary = []
        for slice_ in offer.get("slices", []):
            segments = slice_.get("segments", [])
            itineraries_summary.append(
                {
                    "stops": max(len(segments) - 1, 0),
                    "segments": [
                        {
                            "carrier": seg.get("operating_carrier", {}).get("iata_code")
                            or seg.get("operating_carrier", {}).get("name"),
                            "flight_number": seg.get("flight_number"),
                            "departure_airport": seg.get("origin", {}).get("iata_code"),
                            "departure_time": seg.get("departing_at"),
                            "arrival_airport": seg.get("destination", {}).get("iata_code"),
                            "arrival_time": seg.get("arriving_at"),
                        }
                        for seg in segments
                    ],
                }
            )
        offers.append(
            {
                "offer_id": offer.get("id"),
                "total_price": offer.get("total_amount"),
                "currency": offer.get("total_currency"),
                "itineraries": itineraries_summary,
            }
        )

    offers.sort(key=lambda o: float(o["total_price"]) if o["total_price"] else float("inf"))
    offers = offers[:max_results]

    return {"status": "success", "offer_count": len(offers), "offers": offers}
