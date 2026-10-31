#!/usr/bin/env python3
"""One-request Firecrawl credential smoke test. Never logs the API key or response body."""
import os
import sys

import requests

ENDPOINT = "https://api.firecrawl.dev/v2/search"


def main() -> int:
    key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not key:
        print("FIRECRAWL_AUTH_SMOKE_FAILED reason=missing_secret")
        return 2

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "TrendsUpLoader-auth-smoke/1.0",
    }
    payload = {
        "query": "viral short videos funny unexpected",
        "limit": 1,
        "sources": [{"type": "web"}],
        "tbs": "qdr:d",
    }

    try:
        response = requests.post(
            ENDPOINT, headers=headers, json=payload, timeout=(10, 35)
        )
    except requests.RequestException as exc:
        print(
            "FIRECRAWL_AUTH_SMOKE_FAILED "
            f"reason=network_error exception={type(exc).__name__}"
        )
        return 3

    if response.status_code != 200:
        # Never print the provider body: it may contain account or request details.
        print(
            "FIRECRAWL_AUTH_SMOKE_FAILED "
            f"http_status={response.status_code}"
        )
        return 4

    try:
        data = response.json()
    except ValueError:
        print("FIRECRAWL_AUTH_SMOKE_FAILED reason=non_json_response")
        return 5

    if not isinstance(data, dict) or data.get("success") is False:
        print("FIRECRAWL_AUTH_SMOKE_FAILED reason=provider_rejected_request")
        return 6

    rows = data.get("data")
    if not isinstance(rows, list):
        rows = data.get("results")
    count = len(rows) if isinstance(rows, list) else 0
    print(
        "FIRECRAWL_AUTH_SMOKE_OK "
        f"http_status=200 raw_results={count} credential_present=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
