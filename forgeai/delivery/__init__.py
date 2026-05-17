"""Deployment packaging and Git integration (Phase 10)."""

from forgeai.delivery.git_manager import GitManager, resolve_git_executable
from forgeai.delivery.package_assembler import PackageAssembler
from forgeai.delivery.readme_generator import ReadmeGenerator
from forgeai.delivery.schemas import (
    DeploymentPackage,
    FinalSummaryReport,
    GitCommit,
    RollbackPoint,
)

__all__ = [
    "DeploymentPackage",
    "FinalSummaryReport",
    "GitCommit",
    "GitManager",
    "resolve_git_executable",
    "PackageAssembler",
    "ReadmeGenerator",
    "RollbackPoint",
]
