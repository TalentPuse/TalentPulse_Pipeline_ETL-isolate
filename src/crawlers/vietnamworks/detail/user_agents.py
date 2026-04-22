"""Polite identifying User-Agent for the crawler."""
from src.utils.config import config


def default_headers() -> dict:
    return {
        "User-Agent": config.CRAWLER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
