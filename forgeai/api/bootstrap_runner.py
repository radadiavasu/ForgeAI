"""Background project bootstrap for API-created projects (Phase 10B)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from forgeai.agents.lead_agent import LeadAgent
from forgeai.bootstrap.protocol import AgentBootstrapProtocol
from forgeai.bootstrap.schemas import AgentRecommendation, ApprovedConfig
from forgeai.config import get_settings
from forgeai.database import AsyncSessionFactory
from forgeai.lifecycle.project_registry import ProjectRegistry
from forgeai.llm.client import LLMClient
from forgeai.llm.model_router import ModelRouter
from forgeai.llm.schemas import ModelPool
from forgeai.memory.agent_memory import AgentMemory
from forgeai.memory.task_memory import TaskMemory
from forgeai.models.project import ProjectModel

logger = logging.getLogger(__name__)


async def _auto_approve(rec: AgentRecommendation) -> ApprovedConfig:
    return ApprovedConfig(
        frontend_agent_count=rec.frontend_agent_count,
        backend_agent_count=rec.backend_agent_count,
        qa_agent_count=rec.qa_agent_count,
        approved_by="api_auto",
        approved_at=datetime.now(UTC),
    )


async def run_project_bootstrap(
    project_id: str,
    brief: str,
    constraints: dict,
    name: str,
) -> None:
    """Run bootstrap in a background task; log failures without crashing the server."""
    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        logger.error(
            "Bootstrap skipped for %s: ANTHROPIC_API_KEY not set",
            project_id,
        )
        return

    try:
        pool = ModelPool.from_env()
        router = ModelRouter(pool)
        tm = TaskMemory(settings.redis_url, ttl_seconds=settings.task_memory_ttl)
        memory = AgentMemory(settings.chroma_host, settings.chroma_port)
        llm = LLMClient(settings.anthropic_api_key, router)

        async with AsyncSessionFactory() as session:
            pid = uuid.UUID(project_id)
            res = await session.get(ProjectModel, pid)
            if res is None:
                logger.error("Bootstrap: project %s not found", project_id)
                return

            snapshot = {
                "brief": brief,
                "constraints": constraints,
                "bootstrap_status": "running",
                "pending_approvals": [
                    "Approve technology stack selection",
                    "Approve agent team size",
                ],
            }
            res.project_memory_snapshot = snapshot
            await session.commit()

            lead = LeadAgent(
                "lead_agent_1",
                session,
                task_memory=tm,
                llm_client=llm,
                agent_memory=memory,
            )
            protocol = AgentBootstrapProtocol(lead)
            result = await protocol.run(
                brief,
                constraints,
                _auto_approve,
                project_id=pid,
            )

            await lead.persist_master_and_tech_stack_documents(
                pid,
                result.master_document,
                result.tech_stack_document,
                created_by="api",
            )
            await lead.persist_versioned_artefact(
                pid,
                "api_bootstrap_result",
                {
                    "agents_created": result.agents_created,
                    "task_plan_total": result.task_plan.total_tasks,
                },
                created_by="api",
            )

            display_name = name.strip() or getattr(
                result.master_document,
                "project_name",
                None,
            ) or "New project"
            row = await session.get(ProjectModel, pid)
            if row:
                row.name = display_name
                row.project_memory_snapshot = {
                    "brief": brief,
                    "constraints": constraints,
                    "bootstrap_status": "complete",
                    "pending_approvals": [
                        "Approve technology stack selection",
                        "Approve agent team size",
                        "Approve frontend before backend starts",
                    ],
                }
                await session.commit()

            logger.info("Bootstrap complete for project %s", project_id)
    except Exception:
        logger.exception("Background bootstrap failed for project %s", project_id)
        try:
            async with AsyncSessionFactory() as session:
                pid = uuid.UUID(project_id)
                row = await session.get(ProjectModel, pid)
                if row and isinstance(row.project_memory_snapshot, dict):
                    snap = dict(row.project_memory_snapshot)
                    snap["bootstrap_status"] = "failed"
                    row.project_memory_snapshot = snap
                    await session.commit()
        except Exception:
            logger.exception("Could not persist bootstrap failure for %s", project_id)
