"""Automatic change request classification (Phase 9B)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from forgeai.lifecycle.schemas import (
    ChangeClassification,
    ChangeType,
    ProjectStatus,
    RiskLevel,
)
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument

CHANGE_CLASSIFIER_PROMPT = """
You are Lead_Agent classifying a post-delivery change request.

Analyse the change_request against the master_document and project_status.

Return JSON only:
{
  "change_type": "BUGFIX" | "SMALL_FEATURE" | "LARGE_FEATURE" | "ARCHITECTURAL",
  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "ARCHITECTURAL",
  "reasoning": "plain language explanation",
  "estimated_new_tasks": <integer>
}

Rules:
- BUGFIX: broken behaviour, fix existing code only → usually LOW risk
- SMALL_FEATURE: new capability within existing architecture, 1-3 tasks → LOW or MEDIUM
- LARGE_FEATURE: new components/APIs, 4+ tasks → MEDIUM or HIGH
- ARCHITECTURAL: data model, tech stack, or core structure change → ARCHITECTURAL risk
""".strip()


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    s = _strip_json_fence(text)
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else {"items": out}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else {"items": out}
        raise


def _normalize_classification(data: dict[str, Any]) -> dict[str, Any]:
    ct = str(data.get("change_type", "SMALL_FEATURE")).upper()
    if ct not in ChangeType.__members__:
        ct = ChangeType.SMALL_FEATURE.value
    rl = str(data.get("risk_level", "MEDIUM")).upper()
    if rl not in RiskLevel.__members__:
        rl = RiskLevel.MEDIUM.value
    reasoning = str(data.get("reasoning", "")).strip() or "Classified from change description."
    try:
        est = int(data.get("estimated_new_tasks", 1))
    except (TypeError, ValueError):
        est = 1
    return {
        "change_type": ct,
        "risk_level": rl,
        "reasoning": reasoning,
        "estimated_new_tasks": max(0, est),
    }


class ChangeClassifier:
    """Classify incoming change requests by type and risk."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    async def classify(
        self,
        change_request: str,
        master_document: MasterDocument,
        project_status: ProjectStatus,
    ) -> ChangeClassification:
        user_message = json.dumps(
            {
                "change_request": change_request,
                "project_status": project_status.value,
                "master_document": master_document.model_dump(mode="json"),
            },
            indent=2,
        )[:40000]

        async def _parse(complexity: str) -> ChangeClassification:
            resp = await self.llm.complete(
                system_prompt=CHANGE_CLASSIFIER_PROMPT,
                user_message=user_message,
                complexity=complexity,
                loop_count=0,
                max_tokens=2048,
            )
            raw = _normalize_classification(_extract_json_object(resp.content))
            risk = RiskLevel(raw["risk_level"])
            requires_human = risk != RiskLevel.LOW
            return ChangeClassification(
                change_type=ChangeType(raw["change_type"]),
                risk_level=risk,
                reasoning=raw["reasoning"],
                requires_human_confirmation=requires_human,
                estimated_new_tasks=raw["estimated_new_tasks"],
                classified_at=datetime.now(UTC),
            )

        try:
            return await _parse("LOW")
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return await _parse("MEDIUM")
