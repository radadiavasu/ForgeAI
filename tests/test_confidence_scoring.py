"""Confidence scoring and peer review — mocked LLM."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from forgeai.intelligence.confidence import CONFIDENCE_THRESHOLDS, ConfidenceScorer
from forgeai.intelligence.peer_review import PeerReviewer
from forgeai.llm.schemas import LLMResponse


def _score_json(score: int, rationale: str = "ok") -> str:
    return json.dumps({"score": score, "rationale": rationale})


def _peer_json(*, approved: bool, feedback: str = "fix it") -> str:
    return json.dumps(
        {"approved": approved, "feedback": feedback, "confidence_in_review": 85}
    )


@pytest.fixture
def mock_llm() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_score_returns_confidence_score(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_score_json(82),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    scorer = ConfidenceScorer(mock_llm)
    result = await scorer.score("t1", "backend_1", "backend_agent", "task", "output")
    assert 0 <= result.score <= 100
    assert result.rationale


@pytest.mark.asyncio
async def test_needs_peer_review_false_at_threshold(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_score_json(75),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    scorer = ConfidenceScorer(mock_llm)
    score = await scorer.score("t", "a", "research_agent", "d", "o")
    assert scorer.needs_peer_review(score, "research_agent") is False


@pytest.mark.asyncio
async def test_needs_peer_review_true_below_threshold(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_score_json(55),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    scorer = ConfidenceScorer(mock_llm)
    score = await scorer.score("t", "a", "backend_agent", "d", "o")
    assert scorer.needs_peer_review(score, "backend_agent") is True


def test_qa_agent_threshold_is_80() -> None:
    assert CONFIDENCE_THRESHOLDS["qa_agent"] == 80


def test_research_agent_threshold_is_75() -> None:
    assert CONFIDENCE_THRESHOLDS["research_agent"] == 75


def test_frontend_agent_threshold_is_70() -> None:
    assert CONFIDENCE_THRESHOLDS["frontend_agent"] == 70


@pytest.mark.asyncio
async def test_peer_reviewer_returns_result(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_peer_json(approved=False, feedback="Missing validation"),
        model_used="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    reviewer = PeerReviewer(mock_llm)
    result = await reviewer.review(
        str(uuid.uuid4()),
        "Build endpoint",
        "code",
        "backend_agent_1",
        "peer_backend_agent_1",
    )
    assert isinstance(result.approved, bool)
    assert result.feedback


@pytest.mark.asyncio
async def test_peer_review_uses_medium_complexity(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_peer_json(approved=True, feedback="LGTM"),
        model_used="m",
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=0.0,
    )
    reviewer = PeerReviewer(mock_llm)
    await reviewer.review("t", "task", "out", "a1", "peer_a1")
    assert mock_llm.complete.await_args.kwargs.get("complexity") == "MEDIUM"
