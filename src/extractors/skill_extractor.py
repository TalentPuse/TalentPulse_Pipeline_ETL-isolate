"""
LLM-based skill extraction from job descriptions.

Incremental: only processes jobs not yet in raw.skill_extraction_log.
Batch processing with concurrency control via asyncio.Semaphore.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


@dataclass
class ExtractedSkill:
    name: str
    name_raw: str
    category: str
    importance: str
    confidence: str


@dataclass
class ExtractionResult:
    source: str
    source_job_id: str
    skills: list[ExtractedSkill] = field(default_factory=list)
    experience_years_min: int | None = None
    seniority_level: str | None = None
    model_used: str = ""
    tokens_used: int = 0


def load_config(config_path: str | None = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "configs" / "skill_extraction.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_taxonomy_hint(taxonomy: dict) -> str:
    lines = ["Available categories and example skills:"]
    for cat, skills in taxonomy.items():
        display = ", ".join(skills[:8])
        if len(skills) > 8:
            display += f", ... ({len(skills)} total)"
        lines.append(f"  - {cat}: {display}")
    return "\n".join(lines)


class SkillExtractor:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        llm_cfg = self.config["llm"]
        self.client = AsyncOpenAI(
            api_key=os.getenv(llm_cfg["api_key_env"]),
            base_url=llm_cfg["base_url"],
        )
        self.model = llm_cfg["model"]
        self.max_concurrent = llm_cfg["max_concurrent"]
        self.batch_size = llm_cfg["batch_size"]
        self.timeout = llm_cfg["timeout"]
        self.max_text_length = llm_cfg["max_text_length"]
        self.semaphore = asyncio.Semaphore(self.max_concurrent)

        prompt_cfg = self.config["prompt"]
        taxonomy_hint = build_taxonomy_hint(self.config.get("taxonomy", {}))
        self.system_prompt = (
            prompt_cfg["system"]
            + "\n\n"
            + taxonomy_hint
            + "\n\nJSON schema:\n"
            + prompt_cfg["schema"]
        )

    async def extract_skills(self, text: str) -> dict:
        if not text or not text.strip():
            return {"skills": [], "experience_years_min": None, "seniority_level": None}

        truncated = text[: self.max_text_length]
        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries):
            try:
                async with self.semaphore:
                    response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": self.system_prompt},
                            {"role": "user", "content": f"Phân tích job description:\n\n{truncated}"},
                        ],
                        temperature=0,
                        response_format={"type": "json_object"},
                        timeout=self.timeout,
                    )

                content = response.choices[0].message.content

                # Handle null response
                if content is None:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"LLM returned null response, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error("LLM returned null response after all retries")
                        return {"skills": [], "_tokens_used": 0}

                tokens_used = response.usage.total_tokens if response.usage else 0

                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"LLM returned invalid JSON, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error("LLM returned invalid JSON after all retries")
                        data = {"skills": []}

                data["_tokens_used"] = tokens_used
                return data

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"LLM request failed: {e}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"LLM request failed after all retries: {e}")
                    raise

        return {"skills": [], "_tokens_used": 0}

    def parse_result(self, source: str, source_job_id: str, raw: dict) -> ExtractionResult:
        skills = []
        for s in raw.get("skills", []):
            if not isinstance(s, dict):
                continue
            skills.append(ExtractedSkill(
                name=s.get("name", "").lower().strip(),
                name_raw=s.get("name_raw", ""),
                category=s.get("category", "other"),
                importance=s.get("importance", "mentioned"),
                confidence=s.get("confidence", "medium"),
            ))
        return ExtractionResult(
            source=source,
            source_job_id=source_job_id,
            skills=[s for s in skills if s.name],
            experience_years_min=raw.get("experience_years_min"),
            seniority_level=raw.get("seniority_level"),
            model_used=self.model,
            tokens_used=raw.get("_tokens_used", 0),
        )

    async def process_batch(self, jobs: list[dict]) -> list[ExtractionResult]:
        tasks = [self._process_one(job) for job in jobs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = []
        for job, result in zip(jobs, results):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to extract skills for %s/%s: %s",
                    job["source"], job["source_job_id"], result,
                )
                output.append(ExtractionResult(
                    source=job["source"],
                    source_job_id=job["source_job_id"],
                ))
            else:
                output.append(self.parse_result(job["source"], job["source_job_id"], result))
        return output

    async def _process_one(self, job: dict) -> dict:
        parts = []
        for field_name in self.config["source"]["text_fields"]:
            val = job.get(field_name)
            if val and val.strip():
                parts.append(val.strip())

        combined_text = "\n\n".join(parts)
        return await self.extract_skills(combined_text)
