"""Thin client around the Places API (New) searchNearby endpoint.

Docs: https://developers.google.com/maps/documentation/places/web-service/nearby-search
"""

import time

import requests

SEARCH_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.primaryType",
        "places.types",
        "places.rating",
        "places.userRatingCount",
        "places.businessStatus",
    ]
)

MAX_RESULT_COUNT = 20
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class PlacesApiError(RuntimeError):
    pass


def _is_transient(resp: requests.Response) -> bool:
    if resp.status_code in RETRYABLE_STATUS_CODES:
        return True
    # Right after enabling an API in Google Cloud Console, it can take a few
    # minutes to propagate; during that window calls intermittently 403 with
    # SERVICE_DISABLED even though the API is actually enabled. Worth retrying.
    if resp.status_code == 403 and "SERVICE_DISABLED" in resp.text:
        return True
    return False


class PlacesClient:
    def __init__(
        self,
        api_key: str,
        qps: float = 8.0,
        max_retries: int = 7,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._last_request_at = 0.0

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request_at
        wait = self.min_interval - elapsed
        if wait > 0:
            time.sleep(wait)

    def search_nearby(
        self,
        lat: float,
        lng: float,
        radius_m: float,
        included_types: list[str] | None = None,
    ) -> list[dict]:
        body = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": radius_m,
                }
            },
            "maxResultCount": MAX_RESULT_COUNT,
        }
        if included_types:
            body["includedTypes"] = included_types

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }

        backoff = 1.0
        last_error = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            self._last_request_at = time.monotonic()
            try:
                resp = self.session.post(
                    SEARCH_NEARBY_URL, json=body, headers=headers, timeout=20
                )
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    return resp.json().get("places", [])
                if not _is_transient(resp):
                    raise PlacesApiError(
                        f"searchNearby failed ({resp.status_code}): {resp.text}"
                    )
                last_error = PlacesApiError(
                    f"searchNearby failed ({resp.status_code}): {resp.text}"
                )

            if attempt < self.max_retries:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

        raise PlacesApiError(
            f"searchNearby failed after {self.max_retries + 1} attempts"
        ) from last_error
