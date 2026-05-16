"""Automatic peer review before QA gate (Phase 9)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from forgeai.intelligence.schemas import PeerReviewResult
from forgeai.llm.client import LLMClient

PEER_REVIEW_PROMPT = """
You are a peer reviewer for another developer agent's work.

Review the task and output. Return JSON only:
{
  "approved": boolean,
  "feedback": "<actionable feedback if not approved, or brief confirmation if approved>",
  "confidence_in_review": <integer 0-100>
}
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


def _normalize_peer_review(data: dict[str, Any]) -> dict[str, Any]:
    approved = bool(data.get("approved", False))
    feedback = str(data.get("feedback", "")).strip() or (
        "Approved for QA." if approved else "Needs revision before QA."
    )
    try:
        conf = int(data.get("confidence_in_review", 70))
    except (TypeError, ValueError):
        conf = 70
    conf = max(0, min(100, conf))
    return {"approved": approved, "feedback": feedback, "confidence_in_review": conf}


class PeerReviewer:
    """Peer agent reviews output before it enters the QA gate."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    async def review(
        self,
        task_id: str,
        task_description: str,
        output: str,
        original_agent_id: str,
        reviewer_agent_id: str,
    ) -> PeerReviewResult:
        user_message = (
            f"Original agent: {original_agent_id}\n\n"
            f"Task:\n{task_description}\n\n"
            f"Output:\n{output[:12000]}\n"
        )

        async def _parse(complexity: str) -> PeerReviewResult:
            resp = await self.llm.complete(
                system_prompt=PEER_REVIEW_PROMPT,
                user_message=user_message,
                complexity=complexity,
                loop_count=0,
                max_tokens=2048,
                task_id=task_id,
                agent_id=reviewer_agent_id,
            )
            raw = _normalize_peer_review(_extract_json_object(resp.content))
            return PeerReviewResult(
                task_id=task_id,
                reviewer_agent_id=reviewer_agent_id,
                approved=raw["approved"],
                feedback=raw["feedback"],
                confidence_in_review=raw["confidence_in_review"],
                reviewed_at=datetime.now(UTC),
            )

        try:
            return await _parse("MEDIUM")
        except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return await _parse("MEDIUM")
