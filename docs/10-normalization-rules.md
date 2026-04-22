# Task: Define normalization rules for titles, companies, locations, and dates

## Goal
Write normalization rules that transform parsed VietnamWorks job data into standardized records.

## Focus areas
- title cleaning
- company name cleaning
- location mapping to Ho Chi Minh City
- posted date parsing
- employment type normalization
- seniority normalization
- text cleaning for descriptions and requirements

## What to produce
Return a markdown rulebook containing:
1. Raw-to-normalized mapping rules
2. String cleaning rules
3. Case normalization rules
4. Date parsing rules
5. Location canonicalization rules
6. Fallback behavior
7. Examples before/after
8. Edge cases

## Constraints
- Keep raw fields untouched in source columns
- Do not make irreversible assumptions without recording them
- Support auditability

## Output format
Return markdown with rule tables and examples.