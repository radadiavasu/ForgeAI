"""Drift monitor using semantic similarity between spec and output."""

from __future__ import annotations

import logging

from forgeai.escalation.schemas import DriftCheckResult
from forgeai.monitoring import embeddings

logger = logging.getLogger(__name__)


class DriftMonitor:
    """Compute semantic drift scores and threshold decisions."""

    def __init__(self, threshold: int = 40) -> None:
        self.threshold = threshold

    def compute_drift_score(self, task_specification: str, agent_output: str) -> int:
        """Compute integer semantic drift score in [0, 100]."""
        # SWAP_POINT: replace with Anthropic embeddings API from Phase 5
        similarity = embeddings.compute_similarity(task_specification, agent_output)
        score = int((1 - similarity) * 100)
        bounded_score = max(0, min(100, score))
        logger.info("Drift score computed: %d", bounded_score)
        return bounded_score

    def is_drifting(self, task_specification: str, agent_output: str) -> bool:
        """Return True when drift score exceeds configured threshold."""
        return self.compute_drift_score(task_specification, agent_output) > self.threshold

    def check(self, task_specification: str, agent_output: str) -> DriftCheckResult:
        """Return complete drift evaluation details."""
        score = self.compute_drift_score(task_specification, agent_output)
        drifting = score > self.threshold
        if drifting:
            description = (
                f"Output has diverged significantly from task specification (score: {score})"
            )
        else:
            description = "Output is aligned with task specification"
        return DriftCheckResult(
            score=score,
            is_drifting=drifting,
            threshold=self.threshold,
            description=description,
        )
