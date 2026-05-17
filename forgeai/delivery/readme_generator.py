"""Plain-language README generation (Phase 10, Req 25)."""

from __future__ import annotations

import json
import re
from typing import Any

from forgeai.delivery.schemas import DeploymentPackage
from forgeai.llm.client import LLMClient
from forgeai.llm.schemas import TechStackDocument

README_PROMPT = """
You are writing a README for a non-technical user.

Use plain language only. No jargon (no agent, LLM, PostgreSQL, Chroma, artefact).

Structure exactly:
# {project_name}
{one-sentence description}

## What You Need
- Docker Desktop (download at docker.com)

## Setup
1. Copy `.env.example` to `.env` and fill in your values
2. Run: `docker compose up`
3. Open: http://localhost:3000

## Stopping
Run: `docker compose down`

## Environment Variables
{env_table}

## Built by ForgeAI

The Setup section must have at most 3 numbered steps.
Output markdown only.
""".strip()

_JARGON = frozenset({"agent", "llm", "postgresql", "chroma", "artefact"})


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s.strip()


def _env_table_from_example(env_example: str) -> str:
    rows: list[str] = []
    for line in env_example.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, desc = line.partition("=")
            rows.append(f"| {key.strip()} | {desc.strip()} |")
    if not rows:
        return "| Variable | Description |\n| --- | --- |\n| DATABASE_URL | Database connection |"
    header = "| Variable | Description |\n| --- | --- |"
    return header + "\n" + "\n".join(rows)


def _fallback_readme(
    project_name: str,
    project_brief: str,
    env_example: str,
) -> str:
    env_table = _env_table_from_example(env_example)
    summary = project_brief.split(".")[0].strip() or "A ready-to-run application."
    return f"""# {project_name}

{summary}.

## What You Need
- Docker Desktop (download at docker.com)

## Setup
1. Copy `.env.example` to `.env` and fill in your values
2. Run: `docker compose up`
3. Open: http://localhost:3000

## Stopping
Run: `docker compose down`

## Environment Variables
{env_table}

## Built by ForgeAI
"""


def _setup_step_count(text: str) -> int:
    in_setup = False
    count = 0
    for line in text.splitlines():
        if line.strip().lower().startswith("## setup"):
            in_setup = True
            continue
        if in_setup and line.startswith("## "):
            break
        if in_setup and re.match(r"^\d+\.\s", line.strip()):
            count += 1
    return count


class ReadmeGenerator:
    """Generate a plain-language README for the deployment package."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    async def generate(
        self,
        project_name: str,
        project_brief: str,
        tech_stack: TechStackDocument,
        deployment_package: DeploymentPackage,
        *,
        env_example: str = "",
    ) -> str:
        env_path = deployment_package.env_example_path
        if not env_example and env_path:
            from pathlib import Path

            p = Path(env_path)
            if p.is_file():
                env_example = p.read_text(encoding="utf-8")
        env_table = _env_table_from_example(env_example)
        user_message = json.dumps(
            {
                "project_name": project_name,
                "project_brief": project_brief,
                "tech_stack": tech_stack.model_dump(mode="json"),
                "env_example": env_example,
            },
            indent=2,
        )
        prompt = README_PROMPT.replace("{project_name}", project_name).replace(
            "{env_table}", env_table
        )
        resp = await self.llm.complete(
            system_prompt=prompt,
            user_message=user_message,
            complexity="MEDIUM",
            loop_count=0,
            max_tokens=4096,
        )
        text = _strip_fence(resp.content)
        lower = text.lower()
        if project_name not in text or "docker compose up" not in lower:
            text = _fallback_readme(project_name, project_brief, env_example)
        if any(j in lower for j in _JARGON):
            text = _fallback_readme(project_name, project_brief, env_example)
        if _setup_step_count(text) > 3:
            text = _fallback_readme(project_name, project_brief, env_example)
        return text
