"""Lifecycle schema and prompt contract tests."""

from __future__ import annotations

import uuid

from forgeai.lifecycle.change_classifier import CHANGE_CLASSIFIER_PROMPT
from forgeai.lifecycle.change_executor import CHANGE_SPEC_PROMPT
from forgeai.lifecycle.schemas import (
    ChangeClassification,
    ChangeDecision,
    ChangeHistoryEntry,
    ChangeResult,
    ChangeType,
    HumanChangeApproval,
    ImpactAnalysis,
    PatchResult,
    ProjectStatus,
    RegressionResult,
    RiskLevel,
)


def test_change_classifier_prompt_lists_types() -> None:
    assert "BUGFIX" in CHANGE_CLASSIFIER_PROMPT
    assert "ARCHITECTURAL" in CHANGE_CLASSIFIER_PROMPT


def test_change_spec_prompt_mentions_architect() -> None:
    assert "Architect" in CHANGE_SPEC_PROMPT


def test_project_status_values() -> None:
    assert ProjectStatus.ACTIVE.value == "ACTIVE"
    assert ProjectStatus.LIVE.value == "LIVE"
    assert ProjectStatus.ARCHIVED.value == "ARCHIVED"


def test_change_type_enum_members() -> None:
    assert set(ChangeType.__members__) == {
        "BUGFIX",
        "SMALL_FEATURE",
        "LARGE_FEATURE",
        "ARCHITECTURAL",
    }


def test_risk_level_low_skips_confirmation_in_model() -> None:
    c = ChangeClassification(
        change_type=ChangeType.BUGFIX,
        risk_level=RiskLevel.LOW,
        reasoning="ok",
        requires_human_confirmation=False,
    )
    assert c.requires_human_confirmation is False


def test_risk_level_high_requires_confirmation_in_model() -> None:
    c = ChangeClassification(
        change_type=ChangeType.LARGE_FEATURE,
        risk_level=RiskLevel.HIGH,
        reasoning="big",
        requires_human_confirmation=True,
    )
    assert c.requires_human_confirmation is True


def test_patch_result_defaults_regression_passed() -> None:
    r = PatchResult(project_id="p", change_request="fix")
    assert r.regression_tests_passed is True
    assert r.rework_tasks_completed == []


def test_regression_result_defaults() -> None:
    r = RegressionResult()
    assert r.all_passed is True
    assert r.failures == []


def test_change_result_optional_spec() -> None:
    r = ChangeResult(project_id="p", change_request="add teams")
    assert r.change_spec is None


def test_change_decision_enum() -> None:
    assert ChangeDecision.PROCEED.value == "PROCEED"
    assert ChangeDecision.REJECT.value in ChangeDecision.__members__


def test_impact_analysis_defaults() -> None:
    impact = ImpactAnalysis(
        project_id=str(uuid.uuid4()),
        change_request="x",
        classification=ChangeClassification(
            change_type=ChangeType.BUGFIX,
            risk_level=RiskLevel.LOW,
            reasoning="r",
            requires_human_confirmation=False,
        ),
    )
    assert impact.affected_task_ids == []
    assert impact.conflicting_task_ids == []


def test_human_change_approval_carries_impact() -> None:
    impact = ImpactAnalysis(
        project_id="p",
        change_request="c",
        classification=ChangeClassification(
            change_type=ChangeType.BUGFIX,
            risk_level=RiskLevel.LOW,
            reasoning="r",
            requires_human_confirmation=False,
        ),
    )
    approval = HumanChangeApproval(
        project_id="p",
        change_request="c",
        impact_analysis=impact,
        decision=ChangeDecision.PROCEED,
    )
    assert approval.impact_analysis.change_request == "c"


def test_change_history_entry_outcome_default() -> None:
    impact = ImpactAnalysis(
        project_id="p",
        change_request="c",
        classification=ChangeClassification(
            change_type=ChangeType.BUGFIX,
            risk_level=RiskLevel.LOW,
            reasoning="r",
            requires_human_confirmation=False,
        ),
    )
    entry = ChangeHistoryEntry(
        entry_id="e1",
        project_id="p",
        change_request="c",
        classification=impact.classification,
        impact_analysis=impact,
        human_decision=HumanChangeApproval(
            project_id="p",
            change_request="c",
            impact_analysis=impact,
            decision=ChangeDecision.PROCEED,
        ),
        outcome="PATCH_COMPLETE",
    )
    assert entry.outcome == "PATCH_COMPLETE"
