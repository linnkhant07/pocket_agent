"""Deterministic fictional profiles for later teacher-trace work.

Every row carries the canary string. Emails are @example.com. Nothing here is a real person.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from pocket_agent.paths import CANARY

FIRST = [
    "Avery", "Blair", "Casey", "Drew", "Ellis", "Finley", "Gray", "Harper", "Indigo", "Jules",
    "Kai", "Logan", "Marlow", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Shay", "Taylor",
    "Uma", "Val", "Winter", "Yuri", "Zephyr", "Rowan", "Skyler", "Emerson", "Hayden", "Micah",
]
LAST = [
    "Adler", "Brooks", "Cho", "Diaz", "Ellison", "Frost", "Garcia", "Holt", "Ibarra", "Jung",
    "Kline", "Lopez", "Meyer", "Nguyen", "Okada", "Patel", "Quintero", "Rossi", "Shah", "Tanaka",
    "Ueda", "Vasquez", "Walsh", "Xu", "Young", "Nielsen", "Berg", "Silva", "Khan", "Moreau",
]
SCHOOLS = [
    "Northwind University",
    "Contoso Institute of Technology",
    "Fabrikam College",
    "Adventure Works Polytechnic",
    "Litware State",
    "Coho University",
    "Alpine Technical College",
    "Tailspin School of Computing",
]
FIELDS = ["Computer Science", "Electrical Engineering", "Mathematics", "Data Science", "Physics"]
DEGREES = ["BS", "BS", "BS", "MS", "PhD"]
CITIES = [
    ("Austin", "TX", "US"),
    ("Seattle", "WA", "US"),
    ("Chicago", "IL", "US"),
    ("Toronto", "ON", "CA"),
    ("Berlin", "BE", "DE"),
    ("Bengaluru", "KA", "IN"),
    ("Sao Paulo", "SP", "BR"),
    ("Seoul", "SE", "KR"),
]
COUNTRIES = ["US", "US", "US", "CA", "DE", "IN", "BR", "KR"]
LANGS = ["Python", "TypeScript", "Go", "Java", "SQL", "Rust", "C++"]
FRAMES = ["FastAPI", "React", "Django", "PyTorch", "Node.js"]
ORGS = [
    "Contoso Robotics",
    "Northwind Analytics",
    "Fabrikam Research",
    "Adventure Works",
    "Coho Health",
    "Litware Labs",
    "Tailspin Toys",
    "Woodgrove Bank",
]
ROLES = ["software engineer", "new grad", "intern", "data scientist", "backend engineer"]


def generate_profiles(count: int = 300, seed: int = 428) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    profiles = []
    for index in range(count):
        first = FIRST[index % len(FIRST)]
        last = LAST[(index * 3) % len(LAST)]
        degree = DEGREES[index % len(DEGREES)]
        grad_year = 2026 + (index % 3)
        city, region, city_country = CITIES[index % len(CITIES)]
        country = COUNTRIES[index % len(COUNTRIES)]
        needs_sponsor = country != "US" and index % 5 == 0
        clearance = "secret" if country == "US" and index % 23 == 0 else "none"
        skills_lang = rng.sample(LANGS, k=rng.randint(2, 4))
        skills_frame = rng.sample(FRAMES, k=rng.randint(1, 3))
        org = ORGS[index % len(ORGS)]
        profiles.append(
            {
                "schema_version": 1,
                "canary": CANARY,
                "fictional": True,
                "name": f"{first} {last}",
                "email": f"{first}.{last}.{index}@example.com".lower(),
                "phone": f"+1-555-01{index:02d}"[-12:] if index < 100 else f"+1-555-{index:04d}",
                "location": {
                    "city": city,
                    "region": region,
                    "country": city_country,
                    "willing_to_relocate": index % 2 == 0,
                    "remote_ok": index % 3 != 0,
                },
                "education": [
                    {
                        "school": SCHOOLS[index % len(SCHOOLS)],
                        "degree": degree,
                        "field": FIELDS[index % len(FIELDS)],
                        "graduation": f"{grad_year}-05",
                        "gpa": round(3.0 + (index % 10) / 10, 1),
                    }
                ],
                "citizenship": {
                    "country": country,
                    "authorized_to_work": [country],
                    "requires_sponsorship": needs_sponsor,
                    "clearance": clearance,
                },
                "experience": [
                    {
                        "title": "Software Engineering Intern",
                        "org": org,
                        "start": f"{grad_year - 1}-05",
                        "end": f"{grad_year - 1}-08",
                        "highlights": [f"Built an internal {skills_lang[0]} service for demo data."],
                    }
                ],
                "skills": {"languages": skills_lang, "frameworks": skills_frame},
                "preferences": {
                    "roles": [ROLES[index % len(ROLES)], "new grad"],
                    "locations": [city, "Remote"],
                    "target_start": f"{grad_year}-06",
                },
                "links": {"github": f"https://github.com/example-{first}-{last}-{index}".lower()},
                "constraints": {
                    "notes": "Synthetic fictional profile for Pocket Agent traces. Not a real person.",
                    "salary_expectation": None,
                },
            }
        )
    return profiles


def write_profiles(path: Path, count: int = 300, seed: int = 428) -> int:
    rows = generate_profiles(count=count, seed=seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)
