"""Architect agent — Master_Document and Tech_Stack_Document (Req 02)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from forgeai.agents.base import BaseAgent
from forgeai.agents.research_agent import (
    _as_str,
    _normalize_recommended_stack,
    _normalize_rejected_alternatives,
    _normalize_string_list,
)
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import MasterDocument, ResearchOutput, TechStackDocument
from forgeai.memory.agent_memory import AgentMemory

logger = logging.getLogger(__name__)

ARCHITECT_ROLE_PROMPT = """
You are Architect_Agent, a senior software architect. Your role
is to produce the Master_Document — the authoritative project
specification that all other agents will work from.

You receive research findings from Research_Agent and a project
brief. You produce a complete, unambiguous specification covering
system architecture, component boundaries, data models, and API
surface areas.

You are precise, complete, and consistent. Every component you
define must have clear boundaries. Every API you specify must
have complete request and response schemas.

You output structured JSON only. Never output prose outside
of the JSON structure.
""".strip()

ARCHITECT_TECH_STACK_PROMPT = """
You are Architect_Agent. Produce a formal Tech_Stack_Document JSON object from
the research findings. Fields: language, framework, database, testing_framework,
libraries (array of strings), rationale, rejected_alternatives, version (default 1.0).
Output JSON only.
""".strip()

# Master documents can be large; 8192 output tokens often truncates mid-JSON.
_MASTER_DOCUMENT_MAX_TOKENS_FIRST = 16384
_MASTER_DOCUMENT_MAX_TOKENS_RETRY = 32768
_TECH_STACK_MAX_TOKENS_FIRST = 8192
_TECH_STACK_MAX_TOKENS_RETRY = 16384


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


def _response_likely_truncated(output_tokens: int, max_tokens: int) -> bool:
    """True when the model probably hit the output cap (incomplete JSON)."""
    if max_tokens <= 0:
        return False
    return output_tokens >= max_tokens


def _normalize_master_document_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce common LLM shapes into ``MasterDocument`` fields."""
    out = dict(data)
    if "constraints" in out:
        out["constraints"] = _normalize_string_list(out["constraints"])
    if "tech_stack" in out:
        try:
            out["tech_stack"] = _normalize_recommended_stack(out["tech_stack"])
        except TypeError:
            pass
    return out


def _normalize_tech_stack_document_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce common LLM shapes into ``TechStackDocument`` fields."""
    out = dict(data)
    if "rationale" in out:
        out["rationale"] = _as_str(out["rationale"])
    if "rejected_alternatives" in out:
        out["rejected_alternatives"] = _normalize_rejected_alternatives(out["rejected_alternatives"])
    if "libraries" in out:
        out["libraries"] = _normalize_string_list(out["libraries"])
    return out


class ArchitectAgent(BaseAgent):
    """Produces MasterDocument and TechStackDocument via routed LLM calls."""

    def __init__(
        self,
        agent_id: str,
        db_session,
        llm_client: LLMClient,
        agent_memory: AgentMemory,
    ) -> None:
        super().__init__(agent_id, db_session)
        self.llm = llm_client
        self.memory = agent_memory
        self.agent_role = "architect_agent"

    async def produce_master_document(
        self,
        project_brief: str,
        research_output: ResearchOutput,
        preflight_constraints: dict,
    ) -> MasterDocument:
        """Build MasterDocument from brief, research, and constraints."""
        query = f"{project_brief}\n{research_output.domain_summary}"
        ranked = await self.memory.retrieve_lessons(self.agent_role, query, top_k=5)
        lesson_lines = [f"- {x.lesson.rule}" for x in ranked[:5]]
        lessons_block = "\n".join(lesson_lines) if lesson_lines else "(no prior lessons)"
        system_prompt = f"{ARCHITECT_ROLE_PROMPT}\n\nRelevant past lessons:\n{lessons_block}"

        user_message = (
            f"Project brief:\n{project_brief}\n\n"
            f"Constraints:\n{json.dumps(preflight_constraints)}\n\n"
            f"Research output (JSON):\n{research_output.model_dump_json()}\n\n"
            "Respond with one JSON object: version, project_name, project_summary, "
            "components (name, responsibility, dependencies, acceptance_criteria), "
            "data_models (name, fields with name, type, required, description), "
            "api_surfaces (endpoint, method, request_schema, response_schema, description), "
            "tech_stack (same shape as research recommended_stack), "
            "constraints as an array of strings (not a JSON object), created_at ISO8601 optional.\n\n"
            "Keep the document complete but concise: prefer short bullet-style acceptance_criteria, "
            "minimal request_schema/response_schema (property names and types only, not long prose), "
            "and group related APIs so the JSON stays valid and finishes within the output limit."
        )

        logger.info("[ARCHITECT] Producing Master_Document...")
        doc = await self._complete_json_to_master_document(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        logger.info("[ARCHITECT] Master_Document complete")
        return doc

    async def _complete_json_to_master_document(
        self,
        *,
        system_prompt: str,
        user_message: str,
    ) -> MasterDocument:
        caps = [_MASTER_DOCUMENT_MAX_TOKENS_FIRST, _MASTER_DOCUMENT_MAX_TOKENS_RETRY]
        last_err: json.JSONDecodeError | None = None
        for attempt, max_toks in enumerate(caps):
            resp = await self.llm.complete(
                system_prompt=system_prompt,
                user_message=user_message,
                complexity="HIGH",
                loop_count=0,
                max_tokens=max_toks,
            )
            try:
                raw = _extract_json_object(resp.content)
                data = _normalize_master_document_payload(raw)
                return MasterDocument.model_validate(data)
            except json.JSONDecodeError as e:
                last_err = e
                truncated = _response_likely_truncated(resp.output_tokens, max_toks)
                if truncated and attempt + 1 < len(caps):
                    logger.warning(
                        "[ARCHITECT] Master_Document JSON invalid (likely truncated: "
                        "out_tokens=%s max_tokens=%s). Retrying with max_tokens=%s.",
                        resp.output_tokens,
                        max_toks,
                        caps[attempt + 1],
                    )
                    continue
                logger.error(
                    "[ARCHITECT] Master_Document JSON parse failed: %s",
                    e,
                )
                raise
        assert last_err is not None
        raise last_err

    async def produce_tech_stack_document(self, research_output: ResearchOutput) -> TechStackDocument:
        """Formal tech stack document from research (MEDIUM complexity)."""
        user_message = (
            "Research output JSON:\n"
            f"{research_output.model_dump_json()}\n"
            "Produce Tech_Stack_Document JSON only."
        )
        caps = [_TECH_STACK_MAX_TOKENS_FIRST, _TECH_STACK_MAX_TOKENS_RETRY]
        last_err: json.JSONDecodeError | None = None
        for attempt, max_toks in enumerate(caps):
            resp = await self.llm.complete(
                system_prompt=ARCHITECT_TECH_STACK_PROMPT,
                user_message=user_message,
                complexity="MEDIUM",
                loop_count=0,
                max_tokens=max_toks,
            )
            try:
                raw = _extract_json_object(resp.content)
                data = _normalize_tech_stack_document_payload(raw)
                return TechStackDocument.model_validate(data)
            except json.JSONDecodeError as e:
                last_err = e
                if _response_likely_truncated(resp.output_tokens, max_toks) and attempt + 1 < len(caps):
                    logger.warning(
                        "[ARCHITECT] Tech_Stack JSON invalid (truncated?). Retrying max_tokens=%s.",
                        caps[attempt + 1],
                    )
                    continue
                raise
        assert last_err is not None
        raise last_err
