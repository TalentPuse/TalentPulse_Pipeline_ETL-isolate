"""Data models for the normalization engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MatchResult:
    """Result from a single normalization matcher."""
    value: str
    method: str           # 'source_metadata', 'title_keyword', 'experience_range', 'exact_match', 'default'
    confidence: float     # 0.0 - 1.0
    matched_on: str | None = None


@dataclass
class NormResult:
    """Complete normalization result for one job row."""
    source: str
    source_job_id: str

    # Category
    job_category: str = "Other"
    category_method: str = "default"
    category_confidence: float = 0.0
    category_matched_on: str | None = None

    # Level
    job_level: str = "Mid-level"
    level_method: str = "default"
    level_confidence: float = 0.0
    level_signals: dict[str, Any] = field(default_factory=dict)

    # City
    city_canonical: str | None = None
    city_region: str | None = None
    city_method: str = "default"
    city_confidence: float = 0.0


@dataclass
class DriftEntry:
    """Unmapped value detected during normalization."""
    dimension: str        # 'category', 'level', 'city', 'skill'
    raw_value: str
    source: str
