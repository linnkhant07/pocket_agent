import json
from pathlib import Path

import httpx

from pocket_agent.feeds.boards import parse_greenhouse, parse_lever
from pocket_agent.feeds.cache import CachedHttp
from pocket_agent.feeds.service import fetch_feed
from pocket_agent.feeds.simplify import parse_listings, parse_readme
from pocket_agent.tracker import Tracker


FIXTURES = Path("tests/fixtures")


def test_simplify_listings_and_readme() -> None:
    payload = json.loads((FIXTURES / "recorded_simplify.json").read_text(encoding="utf-8"))
    jobs = parse_listings(payload, "simplify-newgrad")
    assert jobs[0].company == "Example Public Co"
    assert jobs[0].terms == ["Summer 2027"]
    assert jobs[0].posted_at is not None
    html_jobs = parse_readme((FIXTURES / "readme_snippet.html").read_text(encoding="utf-8"), "simplify-newgrad")
    assert html_jobs[0].company == "Contoso Robotics"
    assert html_jobs[0].citizenship_required is True
    assert "Master" in " ".join(html_jobs[0].degrees) or "PhD" in html_jobs[0].degrees
    assert html_jobs[1].active is False
    assert html_jobs[1].sponsorship == "none"
    md_jobs = parse_readme((FIXTURES / "readme_snippet.md").read_text(encoding="utf-8"), "simplify-intern")
    assert md_jobs[0].title == "Software Engineer Intern"
    assert md_jobs[0].url == "https://example.com/jobs/md1"


def test_greenhouse_and_lever_shapes() -> None:
    greenhouse = parse_greenhouse(json.loads((FIXTURES / "recorded_greenhouse.json").read_text(encoding="utf-8")), "example")
    assert greenhouse[0].company == "Example Board"
    assert "Python" in greenhouse[0].description
    assert greenhouse[0].citizenship_required is True
    assert "<" not in greenhouse[0].description
    lever = parse_lever(json.loads((FIXTURES / "recorded_lever.json").read_text(encoding="utf-8")), "example")
    assert lever[0].location == "New York, NY"
    assert lever[0].posted_at is not None
    assert lever[0].id.startswith("lever:example:")


def test_fetch_uses_simplify_fixtures_and_dedupes(tmp_path: Path) -> None:
    tracker = Tracker(tmp_path / "tracker.db")
    fixtures = {
        "new-grad": json.loads(Path("src/pocket_agent/resources/feed_newgrad.json").read_text(encoding="utf-8")),
        "intern": json.loads(Path("src/pocket_agent/resources/feed_intern.json").read_text(encoding="utf-8")),
    }
    first = fetch_feed(tracker, source="simplify", network=False, fixtures=fixtures)
    assert first["ok"] is True
    assert first["inserted"] == 3
    assert first["duplicates"] == 1
    second = fetch_feed(tracker, source="new-grad", network=False, fixtures=fixtures)
    assert second["inserted"] == 0
    assert second["updated"] == 2
    missing = fetch_feed(tracker, source="greenhouse", network=False, fixtures={})
    assert missing["ok"] is False
    assert "company" in missing["error"]


def test_cache_ttl_and_polite_reuse(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text='{"ok": true}', headers={"etag": "abc"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = CachedHttp(tmp_path / "cache", ttl_seconds=3600, min_interval=0, client=client)
    text, cached = cache.get("https://example.com/listings.json")
    assert cached is False
    text2, cached2 = cache.get("https://example.com/listings.json")
    assert cached2 is True
    assert text == text2
    assert calls["n"] == 1
    cache.close()
