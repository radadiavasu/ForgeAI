"""Context window tracking and reduction (Phase 9)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from forgeai.exceptions import ContextWindowExceededError
from forgeai.intelligence.schemas import ContextReductionResult
from forgeai.llm.client import LLMClient

if TYPE_CHECKING:
    from forgeai.memory.task_memory import TaskMemory

logger = logging.getLogger(__name__)

TASK_MEMORY_SUMMARY_PROMPT = """
Summarise the following task-scoped memory entries into a compact digest
(under 500 words). Preserve decisions, defects, and blockers only.
Output plain text only.
""".strip()


class ContextWindowManager:
    MODEL_TOKEN_LIMITS = {
        "claude-haiku-4-5-20251001": 200_000,
        "claude-sonnet-4-6": 200_000,
        "claude-opus-4-6": 200_000,
    }
    WARNING_THRESHOLD = 0.80

    def __init__(self, llm_client: LLMClient, task_memory: TaskMemory) -> None:
        self.llm = llm_client
        self.task_memory = task_memory
        self._reduction_log: list[dict] = []

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def get_limit(self, model: str) -> int:
        return self.MODEL_TOKEN_LIMITS.get(model, 200_000)

    async def check_and_reduce(
        self,
        context: str,
        model: str,
        task_id: str,
        agent_id: str,
        master_doc_section: str | None = None,
    ) -> ContextReductionResult:
        limit = self.get_limit(model)
        warn_at = int(limit * self.WARNING_THRESHOLD)
        original_tokens = self.estimate_tokens(context)
        strategies: list[str] = []
        reduced = context

        if original_tokens <= warn_at:
            return ContextReductionResult(
                original_tokens=original_tokens,
                final_tokens=original_tokens,
                reduction_applied=False,
                strategies_used=[],
                under_limit=original_tokens < limit,
                reduced_context=reduced,
            )

        tokens_before_strategy = self.estimate_tokens(reduced)

        digest = await self._summarise_task_memory(task_id)
        if digest:
            reduced = f"[TASK_MEMORY_DIGEST]\n{digest}"
            strategies.append("task_memory_digest")
            self._log_reduction(
                agent_id,
                task_id,
                "task_memory_digest",
                tokens_before_strategy,
                self.estimate_tokens(reduced),
            )
            tokens_before_strategy = self.estimate_tokens(reduced)

        if master_doc_section and self.estimate_tokens(reduced) > warn_at:
            reduced = f"[MASTER_DOCUMENT_SECTION]\n{master_doc_section}"
            strategies.append("master_doc_section_only")
            self._log_reduction(
                agent_id,
                task_id,
                "master_doc_section_only",
                tokens_before_strategy,
                self.estimate_tokens(reduced),
            )

        final_tokens = self.estimate_tokens(reduced)
        if final_tokens >= limit:
            raise ContextWindowExceededError(
                f"Context {final_tokens} tokens exceeds limit {limit} after all reductions"
            )

        return ContextReductionResult(
            original_tokens=original_tokens,
            final_tokens=final_tokens,
            reduction_applied=bool(strategies),
            strategies_used=strategies,
            under_limit=final_tokens < limit,
            reduced_context=reduced,
        )

    async def _summarise_task_memory(self, task_id: str) -> str:
        stored = await self.task_memory.collect_all(task_id)
        if not stored:
            return ""
        entries = [f"{k}: {v[:2000]}" for k, v in stored.items()]
        blob = "\n".join(entries)
        resp = await self.llm.complete(
            system_prompt=TASK_MEMORY_SUMMARY_PROMPT,
            user_message=blob[:16000],
            complexity="LOW",
            loop_count=0,
            max_tokens=2048,
            task_id=task_id,
            agent_id="context_manager",
        )
        return resp.content.strip()

    def _log_reduction(
        self,
        agent_id: str,
        task_id: str,
        strategy: str,
        tokens_before: int,
        tokens_after: int,
    ) -> None:
        saved = max(0, tokens_before - tokens_after)
        event = {
            "agent_id": agent_id,
            "task_id": task_id,
            "strategy": strategy,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "tokens_saved": saved,
        }
        self._reduction_log.append(event)
        logger.info(
            "Context reduction: agent=%s strategy=%s saved=%s tokens",
            agent_id,
            strategy,
            saved,
        )

    def get_reduction_log(self) -> list[dict]:
        return list(self._reduction_log)
