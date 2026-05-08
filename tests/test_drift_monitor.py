"""Unit tests for DriftMonitor and embedding similarity mapping."""

from __future__ import annotations

from forgeai.monitoring.drift_monitor import DriftMonitor


def test_identical_texts_score_zero() -> None:
    monitor = DriftMonitor(threshold=40)
    assert monitor.compute_drift_score("alpha beta", "alpha beta") == 0


def test_unrelated_texts_score_above_threshold() -> None:
    monitor = DriftMonitor(threshold=40)
    score = monitor.compute_drift_score(
        "JWT validation and role checks", "shopping cart and product catalog"
    )
    assert score > 40


def test_semantically_similar_texts_below_threshold() -> None:
    monitor = DriftMonitor(threshold=40)
    score = monitor.compute_drift_score(
        "Implement JWT token validation and role-based auth",
        "Added JWT verification flow with role authorization checks",
    )
    assert score <= 40


def test_is_drifting_false_below_threshold(monkeypatch) -> None:
    monitor = DriftMonitor(threshold=40)
    monkeypatch.setattr(
        "forgeai.monitoring.embeddings.compute_similarity",
        lambda _a, _b: 0.95,
    )
    assert monitor.is_drifting("spec", "output") is False


def test_is_drifting_true_above_threshold(monkeypatch) -> None:
    monitor = DriftMonitor(threshold=40)
    monkeypatch.setattr(
        "forgeai.monitoring.embeddings.compute_similarity",
        lambda _a, _b: 0.2,
    )
    assert monitor.is_drifting("spec", "output") is True


def test_check_returns_populated_result() -> None:
    monitor = DriftMonitor(threshold=40)
    result = monitor.check(
        "Build JWT token validator",
        "Built shopping cart summary endpoint",
    )
    assert 0 <= result.score <= 100
    assert isinstance(result.is_drifting, bool)
    assert result.threshold == 40
    assert bool(result.description.strip())


def test_description_non_empty_for_both_states(monkeypatch) -> None:
    monitor = DriftMonitor(threshold=40)
    monkeypatch.setattr(
        "forgeai.monitoring.embeddings.compute_similarity",
        lambda _a, _b: 0.9,
    )
    stable = monitor.check("spec", "output")
    monkeypatch.setattr(
        "forgeai.monitoring.embeddings.compute_similarity",
        lambda _a, _b: 0.1,
    )
    drifted = monitor.check("spec", "other")
    assert bool(stable.description.strip())
    assert bool(drifted.description.strip())


def test_custom_threshold_respected(monkeypatch) -> None:
    monitor = DriftMonitor(threshold=70)
    monkeypatch.setattr(
        "forgeai.monitoring.embeddings.compute_similarity",
        lambda _a, _b: 0.4,  # score=60
    )
    assert monitor.is_drifting("spec", "output") is False
