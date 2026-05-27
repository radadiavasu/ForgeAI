from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forgeai.orchestration.integration_qa import IntegrationQAOrchestrator


def _llm_mock() -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(
        return_value=MagicMock(
            content='{"passes": false, "blocking_issues": [], "warnings": [], "recommendation": "check integration"}'
        )
    )
    return llm


@pytest.mark.asyncio
async def test_static_mismatch_detection(tmp_path) -> None:
    orch = IntegrationQAOrchestrator(
        llm_client=_llm_mock(),
        output_dir=str(tmp_path),
        api_contract={"endpoints": [{"method": "GET", "endpoint": "/api/tasks"}]},
        master_document=MagicMock(project_summary="Task app"),
        tech_stack=MagicMock(),
        fe_code_snippets=["fetch('/api/wrong')"],
    )
    with patch(
        "forgeai.orchestration.integration_qa.ComposeStack.up",
        new=AsyncMock(return_value=False),
    ):
        report = await orch.run(project_id="p1")
    assert report.mismatches_found > 0
    assert any("wrong" in m for m in report.smoke_result.contract_mismatches)


@pytest.mark.asyncio
async def test_missing_compose_file(tmp_path) -> None:
    orch = IntegrationQAOrchestrator(
        llm_client=_llm_mock(),
        output_dir=str(tmp_path),
        api_contract={"endpoints": [{"method": "GET", "endpoint": "/api/tasks"}]},
        master_document=MagicMock(project_summary="Task app"),
        tech_stack=MagicMock(),
    )
    report = await orch.run(project_id="p2")
    assert report.passed is False


@pytest.mark.asyncio
async def test_report_has_required_fields(tmp_path) -> None:
    orch = IntegrationQAOrchestrator(
        llm_client=_llm_mock(),
        output_dir=str(tmp_path),
        api_contract={"endpoints": [{"method": "GET", "endpoint": "/api/tasks"}]},
        master_document=MagicMock(project_summary="Task app"),
        tech_stack=MagicMock(),
    )
    with patch(
        "forgeai.orchestration.integration_qa.ComposeStack.up",
        new=AsyncMock(return_value=False),
    ):
        report = await orch.run(project_id="p3")
    assert report.project_id == "p3"
    assert isinstance(report.passed, bool)
    assert isinstance(report.endpoints_checked, int)
    assert isinstance(report.recommendation, str)
