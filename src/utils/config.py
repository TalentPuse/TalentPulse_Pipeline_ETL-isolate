import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
from typing import Optional

# Load environment variables from .env file
load_dotenv()

class Config:
    # Database (PostgreSQL)
    DB_USER: str = os.getenv("DB_USER", "admin")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "password")
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "warehouse")

    # MinIO / S3
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
    S3_ACCESS_KEY: str = os.getenv("S3_ACCESS_KEY", "minioadmin")
    S3_SECRET_KEY: str = os.getenv("S3_SECRET_KEY", "minioadmin")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "talentpulse-raw")

    # Keywords per source (comma-separated in env)
    VNW_KEYWORDS: list[str] = [
        k.strip() for k in os.getenv(
            "VNW_KEYWORDS",
            "Data Engineer,AI Engineer,Business Analyst,Business Development,Technical Sales"
        ).split(",") if k.strip()
    ]
    ITVIEC_KEYWORDS: list[str] = [
        k.strip() for k in os.getenv("ITVIEC_KEYWORDS", "data-engineer,ai-engineer,data-analyst").split(",") if k.strip()
    ]

    # VietnamWorks jobFunctionV3Id filter (comma-separated in env)
    # 25 = "Business/System Analysis", 27 = "Data Engineer/Data Analyst/AI"
    # 129 = "Sales/Business Development", 130 = "Sales Engineer/Technical Sales"
    ALLOWED_FUNCTION_IDS: set[int] = {
        int(x) for x in os.getenv("ALLOWED_FUNCTION_IDS", "25,27,129,130").split(",") if x.strip()
    }

    # Focus keywords for string-based job_function matching
    FOCUS_KEYWORDS: set[str] = {
        k.strip().lower() for k in os.getenv(
            "FOCUS_KEYWORDS",
            "data engineer,data analyst,ai,machine learning,data science,data scientist,big data,analytics,business development,business analyst,technical sales"
        ).split(",") if k.strip()
    }

    # LinkedIn
    LINKEDIN_KEYWORDS: list[str] = [
        k.strip() for k in os.getenv(
            "LINKEDIN_KEYWORDS",
            "Data Engineer,Data Analyst,AI Engineer,Data Scientist,Business Analyst"
        ).split(",") if k.strip()
    ]
    LINKEDIN_GEO_ID: str = os.getenv("LINKEDIN_GEO_ID", "104195383")
    LINKEDIN_RATE_SECONDS: float = float(os.getenv("LINKEDIN_RATE_SECONDS", "3.0"))
    LINKEDIN_PROXY_URL: str | None = os.getenv("LINKEDIN_PROXY_URL")

    # Sources that skip focus validation entirely
    SKIP_FOCUS_SOURCES: set[str] = {
        s.strip() for s in os.getenv("SKIP_FOCUS_SOURCES", "itviec,linkedin").split(",") if s.strip()
    }

    LOCATIONS = ["Ho Chi Minh"]
    TARGET_JOB_FUNCTION_IDS = list(ALLOWED_FUNCTION_IDS)
    # Safety cap on listing pagination (hitsPerPage=50)
    LISTING_MAX_PAGES: int = int(os.getenv("LISTING_MAX_PAGES", "5"))

    # Detail crawler
    CRAWLER_CONTACT_EMAIL: str = os.getenv("CRAWLER_CONTACT_EMAIL", "contact@example.com")
    CRAWLER_USER_AGENT: str = os.getenv(
        "CRAWLER_USER_AGENT",
        f"TalentPulseBot/1.0 (+{os.getenv('CRAWLER_CONTACT_EMAIL', 'contact@example.com')})",
    )
    CRAWLER_RATE_SECONDS: float = float(os.getenv("CRAWLER_RATE_SECONDS", "2.5"))
    CRAWLER_BURST: int = int(os.getenv("CRAWLER_BURST", "2"))
    CRAWLER_RECRAWL_DAYS: int = int(os.getenv("CRAWLER_RECRAWL_DAYS", "7"))
    CRAWLER_KILL_SWITCH: bool = os.getenv("CRAWLER_KILL_SWITCH", "0") == "1"
    CRAWLER_REQUEST_TIMEOUT: int = int(os.getenv("CRAWLER_REQUEST_TIMEOUT", "20"))

    @classmethod
    def get_db_uri(cls) -> str:
        password = quote_plus(cls.DB_PASSWORD)
        return f"postgresql://{cls.DB_USER}:{password}@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}"

config = Config()
