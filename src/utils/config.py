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

    # Target configuration
    TARGET_ROLES = ["Data Engineer", "AI Engineer"]
    LOCATIONS = ["Ho Chi Minh"]
    # VietnamWorks jobFunctionV3Id = 27 is "Data Engineer/Data Analyst/AI"
    TARGET_JOB_FUNCTION_IDS = [27]
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
