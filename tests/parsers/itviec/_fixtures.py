"""Test fixtures for ITviec parser tests."""

import json

SAMPLE_JOB_POSTING = {
    "@context": "http://schema.org",
    "@type": "JobPosting",
    "industry": "Information Technology",
    "title": "Senior Data Engineer",
    "datePosted": "2026-04-24",
    "validThrough": "2099-12-31",
    "skills": "Data Engineer, Google BigQuery, Python, SQL, Spark",
    "description": "<ul><li>Build data pipelines</li><li>Design data models</li></ul>",
    "potentialAction": {
        "@type": "ApplyAction",
        "target": "https://itviec.com/job/senior-data-engineer-acme-corp-4611/job_applications/new",
    },
    "hiringOrganization": {
        "@type": "Organization",
        "logo": "https://itviec.com/rails/active_storage/logo.png",
        "name": "Acme Corp",
        "description": "A great company",
    },
    "employmentType": "FULL_TIME",
    "jobLocation": [
        {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "123 Main St",
                "addressLocality": "Not Available",
                "addressRegion": "Hồ Chí Minh",
                "postalCode": "700000",
                "addressCountry": "VN",
            },
        }
    ],
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "USD",
        "value": {
            "@type": "QuantitativeValue",
            "unitText": "MONTH",
            "value": "You'll love it",
        },
    },
    "directApply": "TRUE",
    "jobBenefits": "<ul><li>Laptop</li><li>Health insurance</li></ul>",
    "experienceRequirements": {
        "@type": "OccupationalExperienceRequirements",
        "monthsOfExperience": 37,
    },
}


def make_detail_html(job_posting: dict | None = None) -> str:
    """Build a minimal ITviec detail page HTML with JSON-LD."""
    data = job_posting or SAMPLE_JOB_POSTING
    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<title>{data.get('title', 'Job')} at {(data.get('hiringOrganization') or {}).get('name', 'Company')} | ITviec</title>
<script type="application/ld+json">{json.dumps({"@type": "BreadcrumbList"})}</script>
<script type="application/ld+json">{json.dumps(data)}</script>
<script type="application/ld+json">{json.dumps({"@type": "WebSite"})}</script>
</head>
<body><h1>{data.get('title', 'Job')}</h1></body>
</html>"""


SAMPLE_LISTING_URLS = [
    "https://itviec.com/it-jobs/senior-data-engineer-acme-corp-4611",
    "https://itviec.com/it-jobs/ai-engineer-python-ml-bigcorp-2549",
    "https://itviec.com/it-jobs/data-analyst-sql-smallco-1234",
]


def make_listing_html(urls: list[str] | None = None, page: int = 1, max_page: int = 2) -> str:
    """Build a minimal ITviec listing page HTML with JSON-LD ItemList."""
    urls = urls or SAMPLE_LISTING_URLS
    item_list = {
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": url}
            for i, url in enumerate(urls)
        ],
    }

    pagination = ""
    if max_page > 1:
        pages_html = []
        for p in range(1, max_page + 1):
            if p == page:
                pages_html.append(f'<div class="page current">{p}</div>')
            else:
                pages_html.append(
                    f'<div class="page"><a href="/it-jobs/test?page={p}&source=search_job">{p}</a></div>'
                )
        pagination = f'<nav class="ipagination">{"".join(pages_html)}</nav>'

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<title>IT Jobs | ITviec</title>
<script type="application/ld+json">{json.dumps(item_list)}</script>
<script type="application/ld+json">{json.dumps({"@type": "BreadcrumbList"})}</script>
</head>
<body>
<div class="job-list">{len(urls)} jobs</div>
{pagination}
</body>
</html>"""
