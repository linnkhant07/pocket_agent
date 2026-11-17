"""Turn a stored value into the string a control expects."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_MONTHS = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}
_MONTH_INDEX = {name.casefold(): number for number, name in _MONTHS.items()}

_STATES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California",
    "co": "Colorado", "ct": "Connecticut", "de": "Delaware", "fl": "Florida", "ga": "Georgia",
    "hi": "Hawaii", "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
    "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri",
    "mt": "Montana", "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire", "nj": "New Jersey",
    "nm": "New Mexico", "ny": "New York", "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio",
    "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
    "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah", "vt": "Vermont",
    "va": "Virginia", "wa": "Washington", "wv": "West Virginia", "wi": "Wisconsin", "wy": "Wyoming",
    "dc": "District of Columbia",
}
_COUNTRIES = {
    "us": "United States", "usa": "United States", "united states": "United States",
    "ca": "Canada", "canada": "Canada",
    "uk": "United Kingdom", "gb": "United Kingdom", "united kingdom": "United Kingdom",
    "mx": "Mexico", "mexico": "Mexico",
}
_DEGREES = {
    "bs": "bachelor", "b.s.": "bachelor", "b.s": "bachelor", "bachelor": "bachelor",
    "bachelor's": "bachelor", "bachelors": "bachelor", "ba": "bachelor", "b.a.": "bachelor",
    "ms": "master", "m.s.": "master", "master": "master", "master's": "master", "masters": "master",
    "phd": "phd", "ph.d.": "phd", "ph.d": "phd", "doctorate": "phd",
    "associate": "associate", "high school": "high school",
}
_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false"}
_EMPTY = {"", "select", "select one", "choose", "choose one", "—", "-"}
# "Decline to self-identify", "I don't wish to answer", "Prefer not to say" are one answer.
_DECLINE = re.compile(
    r"\b(decline|prefer not|rather not|choose not|do not wish|don'?t wish|do not want to (answer|disclose|say)|don'?t want to (answer|disclose|say)|not wish to (answer|disclose|self))\b",
    re.I,
)


def parse_date(value: str) -> tuple[int, int, int] | None:
    text = value.strip()
    match = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)
        return year, month, day
    match = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if match:
        return int(match.group(2)), int(match.group(1)), 1
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        return int(match.group(3)), int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", text)
    if match and match.group(1).casefold() in _MONTH_INDEX:
        return int(match.group(2)), _MONTH_INDEX[match.group(1).casefold()], 1
    return None


def format_date(value: str, *, hint: str = "", input_type: str = "text") -> str:
    parsed = parse_date(value)
    if parsed is None:
        return value
    year, month, day = parsed
    if input_type == "date":
        return f"{year:04d}-{month:02d}-{day:02d}"
    folded = hint.casefold()
    if "yyyy-mm-dd" in folded or input_type == "date":
        return f"{year:04d}-{month:02d}-{day:02d}"
    if "mm/dd/yyyy" in folded:
        return f"{month:02d}/{day:02d}/{year:04d}"
    if "yyyy-mm" in folded:
        return f"{year:04d}-{month:02d}"
    if "month" in folded and "year" in folded:
        return f"{_MONTHS[month]} {year}"
    return f"{month:02d}/{year:04d}"


def date_variants(value: str) -> list[str]:
    parsed = parse_date(value)
    if parsed is None:
        return [value]
    year, month, day = parsed
    month_name = _MONTHS[month]
    variants = [
        f"{month:02d}/{year:04d}",
        f"{year:04d}-{month:02d}",
        f"{month_name} {year}",
        f"{month:02d}/{day:02d}/{year:04d}",
        f"{year:04d}-{month:02d}-{day:02d}",
        value,
    ]
    seen: list[str] = []
    for item in variants:
        if item not in seen:
            seen.append(item)
    return seen


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").casefold().replace("’", "'"))


def _bucket(value: str) -> str:
    text = value.strip().casefold().rstrip(".").replace("’", "'")
    if _DECLINE.search(text):
        return "decline"
    # Whole tokens only. "Not Hispanic" and "Non-Protected" are not "no".
    words = _words(text)
    if text in _YES or words == ["yes"]:
        return "yes"
    if text in _NO or words == ["no"]:
        return "no"
    if text in _STATES:
        return _STATES[text].casefold()
    if text in _COUNTRIES:
        return _COUNTRIES[text].casefold()
    if text in _DEGREES:
        return _DEGREES[text]
    parsed = parse_date(value)
    if parsed:
        return f"{parsed[0]:04d}-{parsed[1]:02d}"
    return re.sub(r"[^a-z0-9]+", "", text)


def _leading_polarity(text: str) -> str | None:
    words = _words(text)
    if not words:
        return None
    if words[0] in {"yes", "y", "true"}:
        return "yes"
    if words[0] in {"no", "n", "false"}:
        return "no"
    return None


def _phrase_in(needle: str, option: str) -> bool:
    wanted = _words(needle)
    haystack = _words(option)
    if not wanted or len(wanted) > len(haystack):
        return False
    width = len(wanted)
    return any(haystack[index:index + width] == wanted for index in range(len(haystack) - width + 1))


def _salary_hits(value: str, options: list[str]) -> list[str]:
    from pocket_agent.forms.facts import parse_salary, salary_contains

    salary = parse_salary(value)
    if salary is None:
        return []
    return [option for option in options if option.strip() and salary_contains(salary, option)]


def _strip_dial_code(option: str) -> str:
    """"United States +1" is the country, with the phone code on the end."""
    return re.sub(r"\s*\(?\+\d{1,4}\)?\s*$", "", option)


# Substring matching these keys picks the wrong option ("No" → "Hispanic", "50000" → "$150,000").
_NO_SUBSTRING = {
    "gender", "hispanic", "race", "veteran_status", "disability_status", "pronouns",
    "salary_expectation", "work_authorization", "requires_sponsorship", "willing_to_relocate",
    "remote_ok", "clearance", "criminal_history", "ssn", "dob", "government_id",
}


def resolve_option(value: str, options: list[str], *, key: str | None = None) -> str | None:
    """Pick an option only when exactly one candidate means the stored value."""
    if not options:
        return None
    # An answer that is literally one of the options wins before any yes/no bucketing ("Yes- Milpitas, CA only").
    wanted = " ".join(value.split()).casefold()
    exact = [option for option in options if wanted and " ".join(option.split()).casefold() == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    salary_hits = _salary_hits(value, options)
    if len(salary_hits) == 1:
        return salary_hits[0]
    if len(salary_hits) > 1:
        return None
    # A salary figure that sits in no band must not match as a substring of a bigger number.
    if re.fullmatch(r"\s*[$£€]?\d[\d,]*(?:\.\d+)?\s*[kKmM]?(?:\s*(?:usd|per year|a year))?\s*", value or "", re.I):
        if any(re.search(r"\d", option) for option in options):
            return None
    target = _bucket(value)
    if target in {"yes", "no"}:
        hits = []
        for option in options:
            if option.strip().casefold() in _EMPTY or _DECLINE.search(option):
                continue
            if _leading_polarity(option) == target or _bucket(option) == target:
                hits.append(option)
        return hits[0] if len(hits) == 1 else None
    bucket_hits = []
    for option in options:
        if option.strip().casefold() in _EMPTY:
            continue
        if _bucket(_strip_dial_code(option)) == target or _bucket(option) == target:
            bucket_hits.append(option)
    if len(bucket_hits) == 1:
        return bucket_hits[0]
    if len(bucket_hits) > 1:
        return None
    if key in _NO_SUBSTRING:
        return None
    phrase_hits = [option for option in options if option.strip().casefold() not in _EMPTY and _phrase_in(value, _strip_dial_code(option))]
    if len(phrase_hits) == 1:
        return phrase_hits[0]
    return None


def _flat_words(text: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", text.casefold())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def closest_option(value: str, options: list[str]) -> str | None:
    """Same words, different punctuation: "University of California, Los Angeles (UCLA)" vs "University of California - Los Angeles"."""
    target = _flat_words(value)
    if not target:
        return None
    best_score, best = 0.0, None
    for option in options:
        words = _flat_words(option)
        if not words or words in _EMPTY:
            continue
        score = 1.0 if words == target else SequenceMatcher(None, target, words).ratio()
        if score > best_score:
            best_score, best = score, option
    return best if best_score >= 0.92 else None


def date_part(label: str, raw: str, *, named: bool) -> str | None:
    """"2027-06" for a field labelled "End date year" is 2027; for "End date month" it is June."""
    parsed = parse_date(raw)
    if parsed is None:
        return None
    year, month, _day = parsed
    has_year = bool(re.search(r"\byear\b", label, re.I))
    has_month = bool(re.search(r"\bmonth\b", label, re.I))
    if has_year == has_month:
        # "Graduation month and year" is one field that takes the whole date.
        return None
    if has_year:
        return str(year)
    return _MONTHS[month] if named else f"{month:02d}"


def equivalent(got: str, expected: str) -> bool:
    if got.strip().casefold() == expected.strip().casefold():
        return True
    if _bucket(got) == _bucket(expected) and _bucket(got) != "":
        return True
    got_digits = re.sub(r"\D", "", got)
    expected_digits = re.sub(r"\D", "", expected)
    if len(got_digits) >= 7 and got_digits == expected_digits:
        return True
    return False


def present_value(value: str, *, kind: str, options: list[str], placeholder: str = "", input_type: str = "text") -> str:
    if kind == "file":
        return "uploaded"
    if kind in {"select", "combobox", "radio"}:
        picked = resolve_option(value, options)
        if picked:
            return picked
        # "No" does not share letters with "I am not a protected veteran", but it is that option.
        from pocket_agent.forms.facts import FACT_KEYS, pick_option

        hits = {option for key in FACT_KEYS if (option := pick_option(key, value, options, ""))}
        return hits.pop() if len(hits) == 1 else ""
    if kind == "checkbox":
        return "Yes" if _bucket(value) == "yes" else "No"
    if kind == "date":
        return format_date(value, hint=placeholder, input_type=input_type)
    return value


def is_blank(value: str) -> bool:
    return value.strip().casefold() in _EMPTY
