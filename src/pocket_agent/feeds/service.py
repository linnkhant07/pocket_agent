"""Fetch feeds into the tracker. Simplify is the default. Greenhouse and Lever need a company."""

from __future__ import annotations

import json
from typing import Any

from pocket_agent.feeds.boards import greenhouse_url, lever_url, parse_greenhouse, parse_lever, require_company
from pocket_agent.feeds.cache import CachedHttp
from pocket_agent.feeds.simplify import LISTS, list_names_for, parse_listings, parse_readme
from pocket_agent.tracker import Tracker


def fetch_feed(
    tracker: Tracker,
    *,
    source: str = "simplify",
    company: str | None = None,
    list_name: str | None = None,
    limit: int | None = None,
    network: bool = True,
    fixtures: dict[str, Any] | None = None,
    http: CachedHttp | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Pull jobs and dedupe them into SQLite.

    `fixtures` is a test/eval map. Keys are `new-grad`, `intern`,
    `greenhouse:<company>`, and `lever:<company>`. When a key is present the
    live URL is not called.
    """
    source = (list_name or source or "simplify").strip().lower()
    try:
        if source in {"simplify", "new-grad", "intern", "simplify-newgrad", "simplify-intern"}:
            return _fetch_simplify(
                tracker,
                source=source,
                limit=limit,
                network=network,
                fixtures=fixtures or {},
                http=http,
                include_inactive=include_inactive,
            )
        if source == "greenhouse":
            return _fetch_board(
                tracker,
                kind="greenhouse",
                company=company,
                limit=limit,
                network=network,
                fixtures=fixtures or {},
                http=http,
            )
        if source == "lever":
            return _fetch_board(
                tracker,
                kind="lever",
                company=company,
                limit=limit,
                network=network,
                fixtures=fixtures or {},
                http=http,
            )
        return {"ok": False, "error": f"unknown source {source!r}"}
    except Exception as exc:  # tool boundary: return the error, do not raise to the model
        return {"ok": False, "error": str(exc), "source": source}


def _fetch_simplify(
    tracker: Tracker,
    *,
    source: str,
    limit: int | None,
    network: bool,
    fixtures: dict[str, Any],
    http: CachedHttp | None,
    include_inactive: bool,
) -> dict[str, Any]:
    names = list_names_for(source)
    summary: dict[str, Any] = {
        "ok": True,
        "source": source,
        "lists": [],
        "inserted": 0,
        "updated": 0,
        "duplicates": 0,
        "inactive_skipped": 0,
        "fetched": 0,
    }
    for name in names:
        jobs, origin = _load_simplify_list(name, network=network, fixtures=fixtures, http=http)
        if limit is not None:
            active = [job for job in jobs if job.active or include_inactive]
            jobs = active[:limit]
        counts = tracker.upsert_jobs(jobs, include_inactive=include_inactive)
        summary["lists"].append({"list": name, "repo": LISTS[name]["repo"], "origin": origin, **counts, "parsed": len(jobs)})
        for key in ("inserted", "updated", "duplicates", "inactive_skipped"):
            summary[key] += counts[key]
        summary["fetched"] += len(jobs)
    return summary


def _load_simplify_list(
    name: str,
    *,
    network: bool,
    fixtures: dict[str, Any],
    http: CachedHttp | None,
) -> tuple[list, str]:
    spec = LISTS[name]
    if name in fixtures:
        return parse_listings(fixtures[name], spec["source"]), "fixture"
    readme_key = f"{name}-readme"
    if readme_key in fixtures:
        return parse_readme(str(fixtures[readme_key]), spec["source"]), "fixture-readme"
    if not network:
        raise RuntimeError(f"offline: no fixture for Simplify list {name}")
    if http is None:
        raise RuntimeError("HTTP cache is not configured")
    try:
        text, cached = http.get(spec["listings_url"])
        payload = json.loads(text)
        return parse_listings(payload, spec["source"]), "listings.json" + (" cache" if cached else "")
    except Exception:
        text, cached = http.get(spec["readme_url"])
        return parse_readme(text, spec["source"]), "readme" + (" cache" if cached else "")


def _fetch_board(
    tracker: Tracker,
    *,
    kind: str,
    company: str | None,
    limit: int | None,
    network: bool,
    fixtures: dict[str, Any],
    http: CachedHttp | None,
) -> dict[str, Any]:
    slug = require_company(company, kind)
    key = f"{kind}:{slug}"
    if key in fixtures:
        payload = fixtures[key]
        origin = "fixture"
    elif not network:
        raise RuntimeError(f"offline: no fixture for {key}")
    else:
        if http is None:
            raise RuntimeError("HTTP cache is not configured")
        url = greenhouse_url(slug) if kind == "greenhouse" else lever_url(slug)
        text, cached = http.get(url)
        payload = json.loads(text)
        origin = "cache" if cached else "network"
    jobs = parse_greenhouse(payload, slug) if kind == "greenhouse" else parse_lever(payload, slug)
    if limit is not None:
        jobs = jobs[:limit]
    counts = tracker.upsert_jobs(jobs)
    return {"ok": True, "source": kind, "company": slug, "origin": origin, "fetched": len(jobs), **counts}
