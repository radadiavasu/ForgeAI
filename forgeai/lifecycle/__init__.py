"""Post-delivery lifecycle and change management (Phase 9B)."""

from forgeai.lifecycle.change_classifier import ChangeClassifier
from forgeai.lifecycle.change_executor import ChangeExecutor, handle_architectural
from forgeai.lifecycle.impact_analyser import ImpactAnalyser
from forgeai.lifecycle.patch_executor import PatchExecutor
from forgeai.lifecycle.project_registry import ProjectRegistry
from forgeai.lifecycle.schemas import (
    ChangeClassification,
    ChangeDecision,
    ChangeHistoryEntry,
    ChangeResult,
    ChangeSpecDocument,
    ChangeType,
    HumanChangeApproval,
    ImpactAnalysis,
    PatchResult,
    Project,
    ProjectStatus,
    RegressionResult,
    RiskLevel,
)

__all__ = [
    "ChangeClassifier",
    "ChangeDecision",
    "ChangeExecutor",
    "ChangeClassification",
    "ChangeHistoryEntry",
    "ChangeResult",
    "ChangeSpecDocument",
    "ChangeType",
    "HumanChangeApproval",
    "ImpactAnalysis",
    "ImpactAnalyser",
    "PatchExecutor",
    "PatchResult",
    "Project",
    "ProjectRegistry",
    "ProjectStatus",
    "RegressionResult",
    "RiskLevel",
    "handle_architectural",
]
