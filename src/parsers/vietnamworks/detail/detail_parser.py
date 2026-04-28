"""Orchestrator: HTML.gz -> JobDetail JSON in MinIO."""
import logging
from datetime import datetime, timezone

from src.parsers.base import MinIOParser
from src.parsers.vietnamworks.detail.html_cleaner import strip_html
from src.parsers.vietnamworks.detail.ref_resolver import resolve
from src.parsers.vietnamworks.detail.rsc_decoder import ParseError, decode, find_main_job_ref
from src.parsers.vietnamworks.detail.schema import JobDetail
from src.storage.minio_client import MinioClient

logger = logging.getLogger(__name__)


class DetailParseError(Exception):
    pass


DETAIL_BASE = "https://www.vietnamworks.com"


def _to_locations(rows: list) -> list[dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "city": r.get("cityName"),
            "city_vi": r.get("cityNameVI"),
            "address": r.get("address"),
            "city_id": r.get("cityId"),
            "district_id": r.get("districtId"),
        })
    return out


def _to_industries(rows: list) -> list[dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "id": r.get("industryId"),
            "name": r.get("industryName"),
            "name_vi": r.get("industryNameVI"),
        })
    return out


def _to_skills(rows: list) -> list[dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "id": r.get("skillId"),
            "name": r.get("skillName"),
            "weight": r.get("skillWeight"),
        })
    return out


def _to_benefits(rows: list) -> list[dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "type": r.get("benefitName") or r.get("benefitType"),
            "value": r.get("benefitValue"),
            "id": r.get("benefitId"),
        })
    return out


def _build_url(alias: str | None, job_id: str) -> str | None:
    if not alias or not job_id:
        return None
    return f"{DETAIL_BASE}/{alias}-{job_id}-jv"


class DetailParser(MinIOParser):
    VERSION = "v2"
    HTML_PREFIX = "details/vietnamworks/html/"
    PARSED_PREFIX = "parsed/details/vietnamworks/"

    def __init__(self, minio: MinioClient | None = None):
        super().__init__(minio)

    def parse_html(self, html: str, source_job_id: str | None = None) -> JobDetail:
        try:
            table = decode(html)
            main = find_main_job_ref(table)
            r = resolve(main, table)
        except ParseError as e:
            raise DetailParseError(str(e)) from e

        smin = r.get("salaryMin")
        smax = r.get("salaryMax")
        is_visible = bool(r.get("isSalaryVisible", False))
        if (smin in (None, 0)) and (smax in (None, 0)):
            is_visible = False
            smin = None
            smax = None

        job_id = str(r.get("jobId") or source_job_id or "")
        alias = r.get("alias")

        return JobDetail(
            source_job_id=job_id,
            source_url=_build_url(alias, job_id),
            parser_version=self.VERSION,
            parsed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            title=r.get("jobTitle"),
            alias=alias,
            company_id=r.get("companyId"),
            company_name=r.get("companyName"),
            company_logo_url=r.get("companyLogo"),
            company_profile_text=strip_html(r.get("companyProfile")),
            salary_min=float(smin) if smin else None,
            salary_max=float(smax) if smax else None,
            salary_currency=r.get("salaryCurrency") or r.get("currency"),
            is_salary_visible=is_visible,
            pretty_salary=r.get("prettySalary") or r.get("salary"),
            job_level=r.get("jobLevel") or r.get("jobLevelVI"),
            years_of_experience=r.get("yearOfExperience") or r.get("yearsOfExperience"),
            employment_type=r.get("typeWorkingId") and str(r.get("typeWorkingId")),
            job_function=r.get("jobFunction") or r.get("jobFunctionName"),
            locations=_to_locations(r.get("workingLocations") or []),
            industries=_to_industries(r.get("industries") or r.get("industriesV3") or []),
            skills=_to_skills(r.get("skills") or []),
            benefits=_to_benefits(r.get("benefits") or []),
            job_description_text=strip_html(r.get("jobDescription")),
            job_requirement_text=strip_html(r.get("jobRequirement")),
            posted_at=r.get("approvedOn") or r.get("createdOn"),
            expired_at=r.get("expiredOn"),
            last_updated_at=r.get("lastUpdatedOn"),
            is_expired=bool(r.get("isExpired", False)),
            num_of_views=r.get("numOfViews"),
            num_of_applications=r.get("numOfApplications"),
            company_size=r.get("companySize"),
            company_size_id=r.get("companySizeId"),
            company_color=r.get("companyColor"),
            num_of_recruits=r.get("numberOfRecruits"),
            salary_period_id=r.get("salaryPeriodId"),
            pretty_salary_vi=r.get("prettySalaryVI"),
            pretty_salary_en=r.get("prettySalaryEN"),
            working_days=r.get("workingDays"),
            working_from_hour=r.get("workingFromHour"),
            working_to_hour=r.get("workingToHour"),
            highest_degree_id=r.get("highestDegreeId"),
            language_selected=r.get("languageSelected"),
            language_selected_vi=r.get("languageSelectedVI"),
            range_age=r.get("rangeAge"),
            primary_address=r.get("address"),
            contact_name=r.get("contactName"),
            contact_email=r.get("emailAddress"),
            required_resume=(bool(r["requiredResume"]) if r.get("requiredResume") is not None else None),
            required_cover_letter=(bool(r["requiredCoverLetter"]) if r.get("requiredCoverLetter") is not None else None),
            services=(r.get("services") or []),
            canonical_slug=r.get("canonical"),
            is_active=r.get("isActive"),
            online_on=r.get("onlineOn"),
        )


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-parse even if JSON exists")
    args = ap.parse_args()
    DetailParser().run_batch(force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    main()
