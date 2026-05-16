"""Seed normalization rule tables from existing CSV seeds + new entries.

Run once after creating the normalization schema:
    python scripts/seed_normalization_rules.py

Idempotent: uses ON CONFLICT DO NOTHING.
"""
import csv
import glob
import logging
import os
import sys

import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.utils.config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
LOG = logging.getLogger("seed_normalization")

SEEDS_DIR = os.path.join(os.path.dirname(__file__), "..", "dbt_transform", "seeds")


def _conn():
    return psycopg2.connect(config.get_db_uri())


# ─── Category rules ───────────────────────────────────────────────

# Problematic keywords that cause false substring matches with LIKE '%keyword%'.
# These are REMOVED and replaced with safer compound patterns.
FALSE_MATCH_KEYWORDS = {
    "html", "css", "figma", "sap", "php", "golang", "ruby",
    "flutter", "epicor", "kafka",
}

# Additional category rules not in the original CSV (word-boundary safe).
EXTRA_CATEGORY_RULES = [
    # Compound patterns that are safer than single-word matches
    ("html/css", "Frontend Developer", 25, "word_boundary"),
    ("html css", "Frontend Developer", 25, "word_boundary"),
    ("sap consultant", "ERP Consultant", 10, "word_boundary"),
    ("sap mm", "ERP Consultant", 15, "word_boundary"),
    ("sap b1", "ERP Consultant", 15, "word_boundary"),
    ("sap analytics", "ERP Consultant", 15, "word_boundary"),
    ("sap pp", "ERP Consultant", 15, "word_boundary"),
    ("flutter developer", "Mobile Developer", 15, "word_boundary"),
    ("kafka", "Data Engineer", 20, "word_boundary"),
    ("apache kafka", "Data Engineer", 18, "word_boundary"),
    ("golang developer", "Backend Developer", 20, "word_boundary"),
    ("go developer", "Backend Developer", 20, "word_boundary"),
    # Missing roles
    ("security engineer", "Other", 10, "word_boundary"),
    ("network engineer", "Other", 10, "word_boundary"),
    ("system administrator", "Other", 10, "word_boundary"),
    ("embedded developer", "Other", 10, "word_boundary"),
    ("game developer", "Other", 10, "word_boundary"),
    ("it support", "Other", 10, "word_boundary"),
    ("graphic designer", "Other", 10, "word_boundary"),
]


def seed_category_rules(conn):
    """Migrate job_title_category_map.csv → normalization.category_rule."""
    csv_path = os.path.join(SEEDS_DIR, "job_title_category_map.csv")
    cur = conn.cursor()

    # Read existing CSV
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    migrated = 0
    skipped = 0
    for row in rows:
        keyword = row["keyword"].strip()
        category = row["job_category"].strip()
        priority = int(row["priority"])

        if keyword.lower() in FALSE_MATCH_KEYWORDS:
            skipped += 1
            LOG.debug(f"Skipping false-match keyword: '{keyword}' → {category}")
            continue

        cur.execute(
            """
            INSERT INTO normalization.category_rule (pattern, job_category, priority, match_mode)
            VALUES (%s, %s, %s, 'word_boundary')
            ON CONFLICT (pattern, job_category) DO NOTHING
            """,
            (keyword, category, priority),
        )
        migrated += 1

    # Add extra rules
    for pattern, category, priority, mode in EXTRA_CATEGORY_RULES:
        cur.execute(
            """
            INSERT INTO normalization.category_rule (pattern, job_category, priority, match_mode)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (pattern, job_category) DO NOTHING
            """,
            (pattern, category, priority, mode),
        )

    conn.commit()
    LOG.info(f"category_rule: migrated {migrated}, skipped {skipped}, added {len(EXTRA_CATEGORY_RULES)} extras")


# ─── Level rules ──────────────────────────────────────────────────

def seed_level_rules(conn):
    """Migrate job_level_map.csv → normalization.level_rule + add raw_field_value and experience_range rules."""
    csv_path = os.path.join(SEEDS_DIR, "job_level_map.csv")
    cur = conn.cursor()

    # Title keyword rules from CSV
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        keyword = row["keyword"].strip()
        level = row["job_level"].strip()
        priority = int(row["priority"])

        cur.execute(
            """
            INSERT INTO normalization.level_rule (signal_type, pattern, job_level, priority, confidence)
            VALUES ('title_keyword', %s, %s, %s, 0.80)
            ON CONFLICT (signal_type, pattern, job_level) DO NOTHING
            """,
            (keyword, level, priority),
        )

    # Raw field value rules (VNW structured data — highest confidence)
    raw_field_rules = [
        ("Intern/Student", "Intern/Student", 0.95),
        ("Internship", "Intern/Student", 0.95),
        ("Fresher/Entry level", "Fresher/Entry level", 0.95),
        ("Entry level", "Fresher/Entry level", 0.90),
        ("Associate", "Fresher/Entry level", 0.85),
        ("Mid-level", "Mid-level", 0.95),
        ("Senior", "Senior", 0.95),
        ("Manager", "Manager", 0.95),
        ("Director", "Director+", 0.95),
        ("Director and above", "Director+", 0.95),
        ("Executive", "Director+", 0.90),
    ]
    for value, level, conf in raw_field_rules:
        cur.execute(
            """
            INSERT INTO normalization.level_rule (signal_type, pattern, job_level, priority, confidence)
            VALUES ('raw_field_value', %s, %s, 5, %s)
            ON CONFLICT (signal_type, pattern, job_level) DO NOTHING
            """,
            (value, level, conf),
        )

    # Experience range rules (low confidence — tiebreaker only)
    exp_rules = [
        ("0-0", "Intern/Student", 0.50),
        ("0-1", "Fresher/Entry level", 0.60),
        ("1-3", "Mid-level", 0.40),
        ("3-7", "Senior", 0.50),
        ("7-99", "Manager", 0.30),
    ]
    for pattern, level, conf in exp_rules:
        cur.execute(
            """
            INSERT INTO normalization.level_rule (signal_type, pattern, job_level, priority, confidence)
            VALUES ('experience_range', %s, %s, 50, %s)
            ON CONFLICT (signal_type, pattern, job_level) DO NOTHING
            """,
            (pattern, level, conf),
        )

    conn.commit()
    LOG.info(f"level_rule: seeded {len(rows)} title_keyword + {len(raw_field_rules)} raw_field + {len(exp_rules)} experience_range")


# ─── City aliases ─────────────────────────────────────────────────

EXTRA_CITY_ALIASES = [
    # Vietnamese diacritics
    ("HCMC", "South", "TP.HCM"),
    ("HCMC", "South", "Tp. Ho Chi Minh"),
    ("HCMC", "South", "thành phố hồ chí minh"),
    ("HCMC", "South", "Sài Gòn"),
    ("HCMC", "South", "Sai Gon"),
    ("Hanoi", "North", "thành phố hà nội"),
    ("Da Nang", "Central", "thành phố đà nẵng"),
    # LinkedIn format
    ("HCMC", "South", "Ho Chi Minh City, Vietnam", "linkedin"),
    ("HCMC", "South", "Ho Chi Minh Metro Area", "linkedin"),
    ("Hanoi", "North", "Hanoi, Vietnam", "linkedin"),
    ("Da Nang", "Central", "Da Nang, Vietnam", "linkedin"),
    ("Binh Duong", "South", "Binh Duong, Vietnam", "linkedin"),
    # ITviec format
    ("HCMC", "South", "Ho Chi Minh", "itviec"),
    # Additional provinces
    ("Quang Ninh", "North", "Quảng Ninh"),
    ("Quang Ninh", "North", "Quang Ninh"),
    ("Hai Duong", "North", "Hải Dương"),
    ("Hai Duong", "North", "Hai Duong"),
    ("Thua Thien Hue", "Central", "Thừa Thiên Huế"),
    ("Thua Thien Hue", "Central", "Thua Thien Hue"),
    ("Khanh Hoa", "Central", "Khánh Hòa"),
    ("Khanh Hoa", "Central", "Khanh Hoa"),
    ("Lam Dong", "Central", "Lâm Đồng"),
    ("Lam Dong", "Central", "Lam Dong"),
    ("Da Lat", "Central", "Đà Lạt"),
    ("Da Lat", "Central", "Da Lat"),
    ("Binh Dinh", "Central", "Bình Định"),
    ("Binh Dinh", "Central", "Binh Dinh"),
    ("Nha Trang", "Central", "Nha Trang"),
    ("Dong Thap", "South", "Đồng Tháp"),
    ("Dong Thap", "South", "Dong Thap"),
    ("An Giang", "South", "An Giang"),
    ("Tay Ninh", "South", "Tây Ninh"),
    ("Tay Ninh", "South", "Tay Ninh"),
    ("Vung Tau", "South", "Vũng Tàu"),
    ("Vung Tau", "South", "Vung Tau"),
    ("Ba Ria-Vung Tau", "South", "Bà Rịa-Vũng Tàu"),
    ("Ba Ria-Vung Tau", "South", "Ba Ria-Vung Tau"),
    ("Nghe An", "North", "Nghệ An"),
    ("Nghe An", "North", "Nghe An"),
    ("Thanh Hoa", "North", "Thanh Hóa"),
    ("Thanh Hoa", "North", "Thanh Hoa"),
    ("Remote", "Remote", "Remote"),
    ("Remote", "Remote", "Nationwide"),
    ("Remote", "Remote", "Work from home"),
    ("Remote", "Remote", "WFH"),
]


def seed_city_aliases(conn):
    """Migrate city_map.csv → normalization.city_alias + add extra variants."""
    csv_path = os.path.join(SEEDS_DIR, "city_map.csv")
    cur = conn.cursor()

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        alias = row["city_raw_en"].strip()
        canonical = row["city_canonical"].strip()
        region = row["region"].strip()
        cur.execute(
            """
            INSERT INTO normalization.city_alias (city_canonical, region, alias)
            VALUES (%s, %s, %s)
            ON CONFLICT (alias, source) DO NOTHING
            """,
            (canonical, region, alias),
        )

    for entry in EXTRA_CITY_ALIASES:
        if len(entry) == 3:
            canonical, region, alias = entry
            source = None
        else:
            canonical, region, alias, source = entry
        cur.execute(
            """
            INSERT INTO normalization.city_alias (city_canonical, region, alias, source)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (alias, source) DO NOTHING
            """,
            (canonical, region, alias, source),
        )

    conn.commit()
    LOG.info(f"city_alias: seeded {len(rows)} from CSV + {len(EXTRA_CITY_ALIASES)} extras")


# ─── Skill synonyms ───────────────────────────────────────────────

SKILL_SYNONYMS = [
    # Abbreviations / expansions
    ("Machine Learning", "ML"),
    ("Machine Learning", "ml"),
    ("Machine Learning", "machine learning"),
    ("Amazon Web Services", "AWS"),
    ("Amazon Web Services", "aws"),
    ("Amazon Web Services", "amazon web services"),
    ("Kubernetes", "K8s"),
    ("Kubernetes", "k8s"),
    ("Kubernetes", "kubernetes"),
    ("PostgreSQL", "postgres"),
    ("PostgreSQL", "postgresql"),
    ("PostgreSQL", "Postgres"),
    ("PostgreSQL", "pg"),
    ("Natural Language Processing", "NLP"),
    ("Natural Language Processing", "nlp"),
    ("Natural Language Processing", "natural language processing"),
    ("Continuous Integration", "CI"),
    ("Continuous Integration", "ci"),
    ("Continuous Delivery", "CD"),
    ("Deep Learning", "deep learning"),
    ("Deep Learning", "dl"),
    ("Computer Vision", "computer vision"),
    ("Computer Vision", "cv"),
    ("Large Language Model", "LLM"),
    ("Large Language Model", "llm"),
    ("Large Language Model", "large language model"),
    ("Large Language Model", "large language models"),
    ("Microsoft Azure", "Azure"),
    ("Microsoft Azure", "azure"),
    ("Google Cloud Platform", "GCP"),
    ("Google Cloud Platform", "gcp"),
    ("Google Cloud Platform", "google cloud"),
    ("Amazon Web Services", "amazon aws"),
    ("Terraform", "terraform"),
    ("Docker", "docker"),
    ("Apache Spark", "spark"),
    ("Apache Spark", "apache spark"),
    ("Apache Kafka", "apache kafka"),
    ("Apache Airflow", "airflow"),
    ("Apache Airflow", "apache airflow"),
    ("Apache Flink", "flink"),
    ("Apache Flink", "apache flink"),
    ("Power BI", "power bi"),
    ("Power BI", "powerbi"),
    ("Power BI", "PowerBI"),
    ("SQL", "sql"),
    ("SQL", "SQL"),
    ("Python", "python"),
    ("Python", "Python"),
    ("Java", "java"),
    ("JavaScript", "javascript"),
    ("JavaScript", "js"),
    ("JavaScript", "JavaScript"),
    ("TypeScript", "typescript"),
    ("TypeScript", "ts"),
    ("TypeScript", "TypeScript"),
    ("React", "react"),
    ("React", "reactjs"),
    ("React", "react.js"),
    ("Vue.js", "vue"),
    ("Vue.js", "vuejs"),
    ("Vue.js", "vue.js"),
    ("Angular", "angular"),
    ("Node.js", "node.js"),
    ("Node.js", "nodejs"),
    ("Node.js", "node"),
    ("Go", "golang"),
    ("Go", "go"),
    ("Rust", "rust"),
    ("C#", "c#"),
    ("C#", "csharp"),
    ("C++", "c++"),
    ("C++", "cpp"),
    ("Git", "git"),
    ("Linux", "linux"),
    ("REST API", "rest api"),
    ("REST API", "restapi"),
    ("GraphQL", "graphql"),
    ("MongoDB", "mongodb"),
    ("MongoDB", "mongo"),
    ("Redis", "redis"),
    ("Elasticsearch", "elasticsearch"),
    ("Elasticsearch", "elastic"),
    ("Jenkins", "jenkins"),
    ("Tableau", "tableau"),
    ("Looker", "looker"),
    ("Snowflake", "snowflake"),
    ("Databricks", "databricks"),
    ("Pandas", "pandas"),
    ("NumPy", "numpy"),
    ("Scikit-learn", "scikit-learn"),
    ("Scikit-learn", "sklearn"),
    ("TensorFlow", "tensorflow"),
    ("TensorFlow", "tf"),
    ("PyTorch", "pytorch"),
    ("FastAPI", "fastapi"),
    ("Django", "django"),
    ("Flask", "flask"),
    ("Spring Boot", "spring boot"),
    ("Spring Boot", "springboot"),
    ("Selenium", "selenium"),
    ("Jira", "jira"),
    ("Agile", "agile"),
    ("Scrum", "scrum"),
    ("DevOps", "devops"),
    ("Microservices", "microservices"),
    ("Microservices", "microservice"),
    ("ETL", "etl"),
    ("Data Warehouse", "data warehouse"),
    ("Data Warehouse", "data warehousing"),
    ("Business Intelligence", "business intelligence"),
    ("Business Intelligence", "bi"),
    ("SAP", "sap"),
    ("Figma", "figma"),
]


def seed_skill_synonyms(conn):
    """Seed normalization.skill_synonym with initial synonym set."""
    cur = conn.cursor()
    for canonical, synonym in SKILL_SYNONYMS:
        cur.execute(
            """
            INSERT INTO normalization.skill_synonym (canonical_name, synonym)
            VALUES (%s, %s)
            ON CONFLICT (synonym) DO NOTHING
            """,
            (canonical, synonym),
        )
    conn.commit()
    LOG.info(f"skill_synonym: seeded {len(SKILL_SYNONYMS)} entries")


# ─── Main ─────────────────────────────────────────────────────────

def main():
    conn = _conn()
    try:
        seed_category_rules(conn)
        seed_level_rules(conn)
        seed_city_aliases(conn)
        seed_skill_synonyms(conn)

        # Summary
        cur = conn.cursor()
        for table in ("category_rule", "level_rule", "city_alias", "skill_synonym"):
            cur.execute(f"SELECT count(*) FROM normalization.{table}")
            n = cur.fetchone()[0]
            LOG.info(f"  normalization.{table}: {n} rows")

        LOG.info("Done. All normalization rules seeded.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
