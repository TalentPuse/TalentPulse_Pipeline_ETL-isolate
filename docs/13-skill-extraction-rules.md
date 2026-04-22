# Task: Design skill extraction rules for job descriptions

## Goal
Create a rule-based skill extraction design using taxonomy v1.

## Inputs
Normalized or parsed job text fields:
- title
- description_text
- requirements_text
- benefits_text

## What to produce
Return a markdown design with:
1. Extraction approach
2. Matching order
3. Text preprocessing rules
4. Synonym matching rules
5. Phrase boundary rules
6. Conflict resolution
7. Output schema for extracted skills
8. Confidence scoring approach
9. Example inputs and outputs

## Constraints
- Rule-based MVP first
- Avoid complex ML extraction in phase 1
- Preserve explainability
- Support bilingual text where possible

## Output format
Return markdown with examples and implementation notes.