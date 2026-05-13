"""Research agent — domain research via LLM (Req 02)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from forgeai.agents.base import BaseAgent
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import ResearchOutput
from forgeai.memory.agent_memory import AgentMemory

logger = logging.getLogger(__name__)

RESEARCH_ROLE_PROMPT = """
You are Research_Agent, a specialist in technology research for
software projects. Your role is to gather domain knowledge,
evaluate technology options, and produce structured research
findings that Architect_Agent will use to design the system.

You are thorough, objective, and evidence-based. You evaluate
at least two technology stack options for every major decision.
You never recommend a technology without explaining why.

You output structured JSON only. Never output prose outside
of the JSON structure.
""".strip()

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
}


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
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            return json.loads(m.group(0))
        raise


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_domain_summary(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return _as_str(raw)
    for key in ("domain_summary", "summary", "overview", "description", "text"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    parts: list[str] = []
    for k, v in raw.items():
        if isinstance(v, str):
            parts.append(f"{k}: {v}")
        elif isinstance(v, (list, dict)):
            parts.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    return " ".join(parts) if parts else json.dumps(raw, ensure_ascii=False)


def _normalize_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, dict):
        out: list[str] = []
        for k, v in raw.items():
            if isinstance(v, (dict, list)):
                out.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
            else:
                out.append(f"{k}: {v}")
        return out
    if not isinstance(raw, list):
        return [_as_str(raw)]
    out = []
    for item in raw:
        if isinstance(item, str):
            if item.strip():
                out.append(item)
        elif isinstance(item, dict):
            url = item.get("url") or item.get("href") or item.get("link")
            title = item.get("title") or item.get("name") or ""
            if isinstance(url, str) and isinstance(title, str) and title and url:
                out.append(f"{title} ({url})")
            elif isinstance(url, str) and url:
                out.append(url)
            elif title:
                out.append(str(title))
            else:
                out.append(json.dumps(item, ensure_ascii=False))
        else:
            out.append(str(item))
    return out


def _normalize_rejected_alternatives(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [_as_str(raw)] if _as_str(raw) else []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            if item.strip():
                out.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("technology") or "alternative"
            reason = item.get("reason") or item.get("rationale") or item.get("reasoning")
            if isinstance(reason, dict | list):
                reason_s = json.dumps(reason, ensure_ascii=False)
            else:
                reason_s = str(reason) if reason else ""
            if reason_s:
                out.append(f"{name}: {reason_s}")
            else:
                out.append(str(name))
        else:
            out.append(str(item))
    return out


def _normalize_recommended_stack(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TypeError("recommended_stack must be a JSON object")
    out = dict(raw)
    if "rationale" in out:
        out["rationale"] = _as_str(out["rationale"])
    if "rejected_alternatives" in out:
        out["rejected_alternatives"] = _normalize_rejected_alternatives(out["rejected_alternatives"])
    return out


def _normalize_research_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce common LLM/web-search JSON shapes into ``ResearchOutput`` fields."""
    out = dict(data)
    if "domain_summary" in out:
        out["domain_summary"] = _normalize_domain_summary(out["domain_summary"])
    if "constraints_respected" in out:
        out["constraints_respected"] = _normalize_string_list(out["constraints_respected"])
    if "research_sources" in out:
        out["research_sources"] = _normalize_string_list(out["research_sources"])
    if "recommended_stack" in out:
        out["recommended_stack"] = _normalize_recommended_stack(out["recommended_stack"])
    return out


class ResearchAgent(BaseAgent):
    """Gathers structured research using ``LLMClient`` and optional web search."""

    def __init__(
        self,
        agent_id: str,
        db_session,
        llm_client: LLMClient | None = None,
        agent_memory: AgentMemory | None = None,
    ) -> None:
        super().__init__(agent_id, db_session)
        self.llm = llm_client
        self.memory = agent_memory
        self.agent_role = "research_agent"

    async def research(self, project_brief: str, preflight_constraints: dict) -> ResearchOutput:
        """Run research with lessons from memory and web search enabled."""
        if self.llm is None or self.memory is None:
            raise RuntimeError("ResearchAgent requires llm_client and agent_memory")
        query = f"{project_brief}\nConstraints: {preflight_constraints!s}"
        ranked = await self.memory.retrieve_lessons(self.agent_role, query, top_k=5)
        lesson_lines = [f"- {x.lesson.rule}" for x in ranked[:5]]
        lessons_block = "\n".join(lesson_lines) if lesson_lines else "(no prior lessons)"

        system_prompt = f"{RESEARCH_ROLE_PROMPT}\n\nRelevant past lessons:\n{lessons_block}"

        user_message = (
            f"Project brief:\n{project_brief}\n\n"
            f"Pre-flight constraints (must respect):\n{json.dumps(preflight_constraints)}\n\n"
            "Respond with ONE flat JSON object only. Types must match exactly:\n"
            "- domain_summary: string (one paragraph; not an object)\n"
            "- technology_options: array of {name, pros, cons, suitable} (pros/cons are string arrays)\n"
            "- recommended_stack: {language, framework, database, testing_framework, rationale, "
            "rejected_alternatives} where rationale is a string and rejected_alternatives is an array "
            "of strings (e.g. [\"FastAPI: reason\"], not objects)\n"
            "- constraints_respected: array of strings listing how each constraint was honoured\n"
            "- research_sources: array of strings (URLs or short citations), not objects\n"
        )

        logger.info("[RESEARCH] Starting research for: %s", project_brief[:80])
        logger.info("[RESEARCH] Web search active — gathering domain knowledge")

        try:
            out = await self._research_with_complexity(
                system_prompt=system_prompt,
                user_message=user_message,
                complexity="LOW",
            )
            logger.info("[RESEARCH] Research complete using LOW tier")
            return out
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            logger.warning(
                "[RESEARCH] LOW tier output invalid (%s). Retrying with MEDIUM tier.",
                type(exc).__name__,
            )
            out = await self._research_with_complexity(
                system_prompt=system_prompt,
                user_message=user_message,
                complexity="MEDIUM",
            )
            logger.info("[RESEARCH] Research complete using MEDIUM fallback")
            return out

    async def _research_with_complexity(
        self,
        *,
        system_prompt: str,
        user_message: str,
        complexity: str,
    ) -> ResearchOutput:
        resp = await self.llm.complete(
            system_prompt=system_prompt,
            user_message=user_message,
            complexity=complexity,
            loop_count=0,
            max_tokens=8192,
            tools=[WEB_SEARCH_TOOL],
        )
        data = _normalize_research_payload(_extract_json_object(resp.content))
        return ResearchOutput.model_validate(data)
