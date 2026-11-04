"""Hit the public feeds once and print counts. Not part of unit tests."""

from __future__ import annotations

import json
import sys

import httpx

URLS = {
    "simplify-newgrad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "simplify-intern": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "greenhouse-stripe": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
    "lever-spotify": "https://api.lever.co/v0/postings/spotify?mode=json",
}


def main() -> int:
    client = httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": "pocket-agent/0.1"})
    ok = True
    for name, url in URLS.items():
        try:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                count = len(payload)
            elif isinstance(payload, dict) and "jobs" in payload:
                count = len(payload["jobs"])
            else:
                count = 1
            print(f"{name}: HTTP {response.status_code} count={count}")
        except Exception as exc:
            ok = False
            print(f"{name}: FAILED {exc}", file=sys.stderr)
    client.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
