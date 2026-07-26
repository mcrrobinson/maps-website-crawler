import requests

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class GeocodeError(RuntimeError):
    pass


def geocode_address(address: str, api_key: str) -> tuple[float, float]:
    resp = requests.get(
        GEOCODE_URL, params={"address": address, "key": api_key}, timeout=15
    )
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status != "OK":
        raise GeocodeError(
            f"Geocoding failed for {address!r}: {status} "
            f"{data.get('error_message', '')}".strip()
        )

    location = data["results"][0]["geometry"]["location"]
    return location["lat"], location["lng"]
