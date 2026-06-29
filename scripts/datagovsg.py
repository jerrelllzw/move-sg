"""Helper to download a dataset from data.gov.sg as parsed GeoJSON.

data.gov.sg serves file downloads via a two-step flow: poll an endpoint for a
short-lived signed URL, then fetch that URL. No API key is required.
"""

import json
import time
import urllib.request

POLL_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/poll-download"


def _get(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "movesg/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download_geojson(dataset_id: str, retries: int = 5, delay: int = 2) -> dict:
    """Resolve a dataset's signed download URL, then fetch and parse the GeoJSON."""
    poll = POLL_URL.format(dataset_id=dataset_id)
    url = None
    for _ in range(retries):
        payload = json.loads(_get(poll))
        url = payload.get("data", {}).get("url")
        if url:
            break
        time.sleep(delay)  # download not ready yet
    if not url:
        raise RuntimeError(f"data.gov.sg returned no download URL for {dataset_id}")
    return json.loads(_get(url, timeout=120).decode("utf-8"))
