"""
Filter parser for Telegram `/add` command.

Grammar (space-separated tokens):
    /add <tokens...>

Tokens classified by heuristics:
  - Pure number + 'm' suffix (or just digits followed by 'm') → min_salary_vnd
    Examples: 20m, 25M, 15.5m → 20000000, 25000000, 15500000
  - Pure number (no suffix, < 10000) assumed million VND
    Examples: 20, 30 → 20000000, 30000000
  - City name (match against canonical list) → city
    Examples: hcmc, hanoi, danang, saigon (alias→HCMC)
  - Level keyword → job_level
    Examples: intern, junior, senior, manager, lead, fresher
  - Everything else → skill (lowercase)
    Examples: python, sql, "data engineer", airflow

Comma inside a token = multi-value for that type:
    python,sql,spark   → skills=[python,sql,spark]
    hcmc,hanoi         → cities=[HCMC, Hanoi]

Examples:
    /add python hcmc 20m
      → {skills:[python], cities:[HCMC], min_salary_vnd:20000000}

    /add python,sql,spark hcmc,hanoi senior 30m
      → {skills:[python,sql,spark], cities:[HCMC,Hanoi],
         job_levels:[Senior], min_salary_vnd:30000000}

    /add "data engineer" airflow hcmc
      → {skills:[data engineer, airflow], cities:[HCMC]}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ─── Lookup tables ─────────────────────────────────────────────────
# Canonical city names (lowercase alias → canonical form).
# Keep in sync with dbt_transform/seeds/city_map.csv.
CITY_ALIASES: dict[str, str] = {
    "hcmc": "HCMC",
    "hochiminh": "HCMC",
    "hcm": "HCMC",
    "saigon": "HCMC",
    "sg": "HCMC",
    "tphcm": "HCMC",
    "hanoi": "Hanoi",
    "hn": "Hanoi",
    "danang": "Da Nang",
    "dn": "Da Nang",
    "haiphong": "Hai Phong",
    "cantho": "Can Tho",
}

# Level keywords → canonical label (match dbt_dev_silver.silver_job_detail.job_level)
LEVEL_ALIASES: dict[str, str] = {
    "intern": "Intern/Student",
    "student": "Intern/Student",
    "fresher": "Fresher/Entry level",
    "entry": "Fresher/Entry level",
    "junior": "Experienced (non-manager)",
    "experienced": "Experienced (non-manager)",
    "senior": "Experienced (non-manager)",  # VNW data: senior often classified here
    "lead": "Manager",
    "manager": "Manager",
    "director": "Director",
    "chief": "C-Level",
    "cto": "C-Level",
    "cio": "C-Level",
}

# Numeric suffix multipliers
SALARY_SUFFIXES = {"m": 1_000_000, "tr": 1_000_000, "k": 1_000, "b": 1_000_000_000}


# ─── Parsed filter ─────────────────────────────────────────────────
@dataclass
class ParsedFilter:
    skills: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    job_levels: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    min_salary_vnd: Optional[int] = None
    raw_tokens: list[str] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True if no filter criteria set at all."""
        return not any(
            [
                self.skills,
                self.cities,
                self.job_levels,
                self.companies,
                self.min_salary_vnd,
            ]
        )

    def to_subscription_args(self) -> dict:
        """Convert to kwargs for INSERT INTO user_alerts.subscriptions."""
        return {
            "skills": self.skills or None,
            "cities": self.cities or None,
            "job_levels": self.job_levels or None,
            "companies": self.companies or None,
            "min_salary_vnd": self.min_salary_vnd,
        }

    def describe(self) -> str:
        """Human-readable summary for bot reply."""
        parts = []
        if self.skills:
            parts.append(f"🔧 Skills: {', '.join(self.skills)}")
        if self.cities:
            parts.append(f"📍 Cities: {', '.join(self.cities)}")
        if self.job_levels:
            parts.append(f"💼 Levels: {', '.join(self.job_levels)}")
        if self.companies:
            parts.append(f"🏢 Companies: {', '.join(self.companies)}")
        if self.min_salary_vnd:
            parts.append(f"💰 Min salary: {self.min_salary_vnd / 1_000_000:.0f}M VND")
        return "\n".join(parts) if parts else "(no filters — match all new jobs)"


# ─── Token classifier ──────────────────────────────────────────────
_SALARY_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([a-zA-Z]*)$")


def _parse_salary(token: str) -> Optional[int]:
    """Parse '20m', '25M', '15.5m', '30tr', '20' → VND bigint."""
    m = _SALARY_RE.match(token.strip())
    if not m:
        return None
    num_str, suffix = m.group(1).replace(",", "."), m.group(2).lower()
    try:
        num = float(num_str)
    except ValueError:
        return None

    if suffix in SALARY_SUFFIXES:
        return int(num * SALARY_SUFFIXES[suffix])

    # No suffix: assume millions if reasonable range (salary-like)
    if not suffix and 1 <= num < 10_000:
        return int(num * 1_000_000)
    return None


def _classify_single(token: str) -> tuple[str, object]:
    """
    Classify a single token into (type, value).
    Returns ('skill'|'city'|'level'|'salary'|'unknown', value).
    """
    lower = token.lower().strip()
    if not lower:
        return ("unknown", token)

    # Salary: must have 'm'/'tr'/'b' suffix or be a pure small number
    salary = _parse_salary(lower)
    if salary is not None:
        return ("salary", salary)

    if lower in CITY_ALIASES:
        return ("city", CITY_ALIASES[lower])

    if lower in LEVEL_ALIASES:
        return ("level", LEVEL_ALIASES[lower])

    # Default: treat as skill (preserve lowercase)
    return ("skill", lower)


def parse_filter(text: str) -> ParsedFilter:
    """
    Parse user input after the `/add` command.

    Splits by whitespace; each token may contain commas for multi-value.
    """
    result = ParsedFilter()
    if not text or not text.strip():
        return result

    tokens = text.strip().split()
    result.raw_tokens = list(tokens)

    for token in tokens:
        # Handle comma-separated multi-value within a token
        parts = [p.strip() for p in token.split(",") if p.strip()]
        for part in parts:
            type_, value = _classify_single(part)
            if type_ == "skill":
                result.skills.append(value)  # type: ignore[arg-type]
            elif type_ == "city":
                if value not in result.cities:
                    result.cities.append(value)  # type: ignore[arg-type]
            elif type_ == "level":
                if value not in result.job_levels:
                    result.job_levels.append(value)  # type: ignore[arg-type]
            elif type_ == "salary":
                # Keep the max if multiple given (user might type "20 30m" by mistake)
                v = value  # type: ignore[assignment]
                if result.min_salary_vnd is None or v > result.min_salary_vnd:  # type: ignore[operator]
                    result.min_salary_vnd = int(v)  # type: ignore[arg-type]
            else:
                result.unrecognized.append(part)

    # Dedup skills case-insensitively (already lowercase)
    result.skills = list(dict.fromkeys(result.skills))

    return result
