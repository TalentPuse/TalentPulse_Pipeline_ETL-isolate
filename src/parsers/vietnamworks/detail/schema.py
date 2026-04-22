"""Schema for parsed VietnamWorks job details."""
from dataclasses import dataclass, field, asdict


@dataclass
class JobDetail:
    source: str = "vietnamworks"
    source_job_id: str = ""
    source_url: str | None = None
    parser_version: str = "v1"
    parsed_at: str = ""

    # Core
    title: str | None = None
    alias: str | None = None
    company_id: int | None = None
    company_name: str | None = None
    company_logo_url: str | None = None
    company_profile_text: str | None = None

    # Compensation
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    is_salary_visible: bool = False
    pretty_salary: str | None = None

    # Job profile
    job_level: str | None = None
    years_of_experience: int | None = None
    employment_type: str | None = None
    job_function: str | None = None

    # Locations & taxonomy
    locations: list[dict] = field(default_factory=list)
    industries: list[dict] = field(default_factory=list)
    skills: list[dict] = field(default_factory=list)
    benefits: list[dict] = field(default_factory=list)

    # Long text
    job_description_text: str | None = None
    job_requirement_text: str | None = None

    # Lifecycle
    posted_at: str | None = None
    expired_at: str | None = None
    last_updated_at: str | None = None
    is_expired: bool = False

    # Engagement
    num_of_views: int | None = None
    num_of_applications: int | None = None

    # --- v2: Tier-1 expanded fields ---
    # Company extras
    company_size: str | None = None
    company_size_id: int | None = None
    company_color: str | None = None

    # Recruitment volume
    num_of_recruits: int | None = None

    # Salary nuance
    salary_period_id: int | None = None
    pretty_salary_vi: str | None = None
    pretty_salary_en: str | None = None

    # Working schedule
    working_days: str | None = None
    working_from_hour: str | None = None
    working_to_hour: str | None = None

    # Requirements
    highest_degree_id: int | None = None
    language_selected: str | None = None
    language_selected_vi: str | None = None
    range_age: str | None = None
    required_resume: bool | None = None
    required_cover_letter: bool | None = None

    # Contact & primary address
    primary_address: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None

    # Promotion / lifecycle
    services: list[dict] = field(default_factory=list)
    canonical_slug: str | None = None
    is_active: bool | None = None
    online_on: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
