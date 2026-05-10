"""Tests for ModelRouter tier and loop_count rules."""

import pytest

from forgeai.llm.model_router import ModelRouter
from forgeai.llm.schemas import ModelPool, TierPool


@pytest.fixture
def env_like_pool() -> ModelPool:
    """Distinct models per tier/slot for assertions."""
    return ModelPool(
        low=TierPool(default="MODEL_LOW_DEFAULT", escalated="MODEL_LOW_ESCALATED"),
        medium=TierPool(
            default="MODEL_MEDIUM_DEFAULT",
            escalated="MODEL_MEDIUM_ESCALATED",
        ),
        high=TierPool(default="MODEL_HIGH_DEFAULT", escalated="MODEL_HIGH_ESCALATED"),
    )


def test_low_loop_0_default(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    assert r.route("LOW", 0) == "MODEL_LOW_DEFAULT"


def test_low_loop_2_escalated(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    assert r.route("LOW", 2) == "MODEL_LOW_ESCALATED"


def test_medium_loop_0_default(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    assert r.route("MEDIUM", 0) == "MODEL_MEDIUM_DEFAULT"


def test_medium_loop_2_escalated(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    assert r.route("MEDIUM", 2) == "MODEL_MEDIUM_ESCALATED"


def test_high_loop_0_default(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    assert r.route("HIGH", 0) == "MODEL_HIGH_DEFAULT"


def test_high_loop_2_escalated(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    assert r.route("HIGH", 2) == "MODEL_HIGH_ESCALATED"


def test_low_never_returns_high_tier_model(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    for lc in (0, 1, 2, 10, 999):
        m = r.route("LOW", lc)
        assert m.startswith("MODEL_LOW")
        assert "HIGH" not in m


def test_get_tier_ceiling(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    assert r.get_tier_ceiling("LOW") == "MODEL_LOW_ESCALATED"
    assert r.get_tier_ceiling("MEDIUM") == "MODEL_MEDIUM_ESCALATED"
    assert r.get_tier_ceiling("HIGH") == "MODEL_HIGH_ESCALATED"


def test_invalid_complexity_raises(env_like_pool: ModelPool) -> None:
    r = ModelRouter(env_like_pool)
    with pytest.raises(ValueError):
        r.route("XL", 0)
    with pytest.raises(ValueError):
        r.get_tier_ceiling("bogus")
