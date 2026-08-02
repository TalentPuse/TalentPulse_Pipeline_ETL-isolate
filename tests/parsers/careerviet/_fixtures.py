"""Fixtures copied from live CareerViet pages on 2026-08-02.

Kept verbatim, quirks included — these are the exact shapes the parser has to
survive, and inventing tidier ones would test a site that does not exist.
"""

# Job 35C7EA19: district in addressRegion, province in addressLocality,
# salary as a label, double-encoded employmentType, months of experience.
JOB_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"WebSite","name":"CareerViet"}
</script>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Data Analyst (Growth Analytics)",
  "description": "Phan tich du lieu tang truong, xay dung dashboard.",
  "datePosted": "2026-07-13T02:29:26.660Z",
  "validThrough": "2026-08-15T23:59:00Z",
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "VND",
    "value": {"@type": "QuantitativeValue", "value": "C\\u1ea1nh tranh", "unitText": "MONTH"}
  },
  "industry": "IT - Phan mem, Ngan hang",
  "jobBenefits": "Bao hiem, Thuong",
  "experienceRequirements": {
    "@type": "OccupationalExperienceRequirements",
    "monthsOfExperience": 36,
    "description": "Kinh nghiem 3 Nam"
  },
  "url": "https://careerviet.vn/vi/tim-viec-lam/data-analyst-growth-analytics.35C7EA19.html",
  "identifier": {"@type": "PropertyValue", "name": "FPT LONG CHAU", "value": "35C7EA19"},
  "hiringOrganization": {"@type": "Organization", "name": "FPT LONG CHAU"},
  "employmentType": ["\\"FULL_TIME\\""],
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "streetAddress": "Qu\\u1eadn 7, H\\u1ed3 Ch\\u00ed Minh",
      "addressRegion": "Qu\\u1eadn 7",
      "addressLocality": "H\\u1ed3 Ch\\u00ed Minh",
      "addressCountry": "VN"
    }
  },
  "skills": "Data Analyst, Ki\\u1ebfn tr\\u00fac s\\u01b0, Structural Drafter",
  "workHours": "8:00-17:00"
}
</script>
</head><body></body></html>
"""

# A posting with a real numeric salary band and no district component.
JOB_HTML_NUMERIC_SALARY = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior Data Engineer",
  "datePosted": "2026-07-28T20:02:37.032Z",
  "validThrough": "2026-08-31T23:59:00Z",
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "USD",
    "value": {"@type": "QuantitativeValue", "minValue": 2000, "maxValue": 3500, "unitText": "MONTH"}
  },
  "identifier": {"@type": "PropertyValue", "name": "OCB", "value": "35C7F173"},
  "hiringOrganization": {"@type": "Organization", "name": "OCB"},
  "employmentType": "FULL_TIME",
  "jobLocation": {
    "@type": "Place",
    "address": {"@type": "PostalAddress", "streetAddress": "H\\u1ed3 Ch\\u00ed Minh",
                "addressRegion": "H\\u1ed3 Ch\\u00ed Minh", "addressLocality": "H\\u1ed3 Ch\\u00ed Minh",
                "addressCountry": "VN"}
  }
}
</script>
</head><body></body></html>
"""

HTML_NO_JOBPOSTING = """
<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite"}</script>
</head><body></body></html>
"""
