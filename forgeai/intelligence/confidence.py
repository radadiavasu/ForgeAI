"""Agent self-reported confidence scoring (Phase 9)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from forgeai.intelligence.schemas import ConfidenceScore
from forgeai.llm.client import LLMClient

CONFIDENCE_THRESHOLDS = {
    "qa_agent": 80,
    "research_agent": 75,
    "architect_agent": 75,
    "frontend_agent": 70,
    "backend_agent": 70,
    "lead_agent": 70,
}

CONFIDENCE_SCORING_PROMPT = """
You are an AI developer agent rating your own work output.

Given the task description and your output, return JSON only:
{
  "score": <integer 0-100>,
  "rationale": "<plain language explanation>"
}

Score honestly: 90+ only when output fully meets requirements with no gaps.
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


def _normalize_confidence(data: dict[str, Any]) -> dict[str, Any]:
    score = data.get("score", 50)
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 50
    score = max(0, min(100, score))
    rationale = str(data.get("rationale", "")).strip() or "No rationale provided."
    return {"score": score, "rationale": rationale}


class ConfidenceScorer:
    """LLM-assisted self-confidence rating for agent outputs."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    async def score(
        self,
        task_id: str,
        agent_id: str,
        agent_role: str,
        task_description: str,
        output: str,
    ) -> ConfidenceScore:
        user_message = (
            f"Agent role: {agent_role}\n\n"
            f"Task:\n{task_description}\n\n"
            f"Output:\n{output[:12000]}\n"
        )

        async def _parse(complexity: str) -> ConfidenceScore:
            resp = await self.llm.complete(
                system_prompt=CONFIDENCE_SCORING_PROMPT,
                user_message=user_message,
                complexity=complexity,
                loop_count=0,
                max_tokens=1024,
                task_id=task_id,
                agent_id=agent_id,
            )
            raw = _normalize_confidence(_extract_json_object(resp.content))
            return ConfidenceScore(
                score=raw["score"],
                agent_id=agent_id,
                task_id=task_id,
                rationale=raw["rationale"],
                scored_at=datetime.now(UTC),
            )

        try:
            return await _parse("LOW")
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return await _parse("MEDIUM")

    def get_threshold(self, agent_role: str) -> int:
        return CONFIDENCE_THRESHOLDS.get(agent_role, 70)

    def needs_peer_review(self, score: ConfidenceScore, agent_role: str) -> bool:
        return score.score < self.get_threshold(agent_role)
