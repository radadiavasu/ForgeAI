"""ARCHITECTURAL change escalation tests."""

from __future__ import annotations

import uuid

import pytest

from forgeai.lifecycle.change_executor import handle_architectural
from forgeai.lifecycle.schemas import (
    ChangeClassification,
    ChangeDecision,
    ChangeType,
    ImpactAnalysis,
    RiskLevel,
)


def _impact() -> ImpactAnalysis:
    return ImpactAnalysis(
        project_id=str(uuid.uuid4()),
        change_request="Migrate to graph DB",
        classification=ChangeClassification(
            change_type=ChangeType.ARCHITECTURAL,
            risk_level=RiskLevel.ARCHITECTURAL,
            reasoning="structural",
            requires_human_confirmation=True,
        ),
        human_message="This change restructures how data is stored.",
    )


@pytest.mark.asyncio
async def test_handle_architectural_proceed() -> None:
    async def _cb(_msg: str) -> ChangeDecision:
        return ChangeDecision.PROCEED

    decision = await handle_architectural(_impact(), "proj", _cb)
    assert decision == ChangeDecision.PROCEED


@pytest.mark.asyncio
async def test_handle_architectural_reject() -> None:
    async def _cb(_msg: str) -> ChangeDecision:
        return ChangeDecision.REJECT

    decision = await handle_architectural(_impact(), "proj", _cb)
    assert decision == ChangeDecision.REJECT


@pytest.mark.asyncio
async def test_handle_architectural_report_contains_human_message(capsys) -> None:
    async def _cb(_msg: str) -> ChangeDecision:
        assert "restructures" in _msg or "graph" in _msg.lower() or "data" in _msg.lower()
        return ChangeDecision.REJECT

    await handle_architectural(_impact(), "proj", _cb)
    captured = capsys.readouterr()
    assert "STRUCTURAL CHANGE" in captured.out
