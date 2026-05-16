"""API contract validation — mocked LLM, no Sandbox."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.agents.qa_agent import QAAgent
from forgeai.llm.schemas import LLMResponse
from forgeai.orchestration.backend_orchestrator import ContractValidator
from forgeai.sandbox.schemas import RunnerOutput


def _contract() -> dict:
    return {
        "endpoints": [
            {
                "method": "GET",
                "path": "/tasks",
                "response": {"fields": ["id", "title", "created_at"]},
            }
        ]
    }


def _validation_json(*, valid: bool, violations: list[str], severity: str = "blocking") -> str:
    return json.dumps(
        {"valid": valid, "violations": violations, "severity": severity}
    )


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    return llm


@pytest.mark.asyncio
async def test_contract_validator_valid_when_code_matches(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(valid=True, violations=[]),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    validator = ContractValidator(mock_llm)
    result = await validator.validate(
        "def get_tasks(): return []",
        _contract(),
        "List tasks",
    )
    assert result.valid is True
    assert result.violations == []


@pytest.mark.asyncio
async def test_contract_validator_invalid_endpoint_mismatch(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(
            valid=False,
            violations=["Endpoint path is /items but contract requires /tasks"],
        ),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    validator = ContractValidator(mock_llm)
    result = await validator.validate("path = '/items'", _contract(), "List tasks")
    assert result.valid is False
    assert len(result.violations) >= 1


@pytest.mark.asyncio
async def test_contract_validator_invalid_missing_response_field(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(
            valid=False,
            violations=["Response missing required field created_at"],
        ),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    validator = ContractValidator(mock_llm)
    result = await validator.validate("return {'id': 1}", _contract(), "List tasks")
    assert result.valid is False
    assert any("created_at" in v for v in result.violations)


@pytest.mark.asyncio
async def test_contract_validator_invalid_http_method(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(
            valid=False,
            violations=["Uses POST but contract requires GET"],
        ),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    validator = ContractValidator(mock_llm)
    result = await validator.validate("@app.post('/tasks')", _contract(), "List tasks")
    assert result.valid is False


@pytest.mark.asyncio
async def test_blocking_violation_skips_sandbox(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(valid=False, violations=["Wrong path"]),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    runner = MagicMock()
    runner.run = AsyncMock()
    qa = QAAgent(
        "qa_1",
        db_session,
        test_runner=runner,
        contract_validator=ContractValidator(mock_llm),
    )
    out = await qa.review(
        uuid.uuid4(),
        "code",
        "def test_x(): pass",
        development_phase="BACKEND_PHASE",
        api_contract=_contract(),
        task_description="List tasks",
    )
    runner.run.assert_not_called()
    assert out.success is False
    assert out.total_tests == 0


@pytest.mark.asyncio
async def test_warning_violation_proceeds_to_sandbox(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(
            valid=False,
            violations=["Optional field naming differs"],
            severity="warning",
        ),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    runner = MagicMock()
    runner.run = AsyncMock(
        return_value=RunnerOutput(
            success=True,
            total_tests=1,
            passed_tests=1,
            failed_tests=0,
            test_cases=[],
            stdout="ok",
            stderr="",
            execution_time_seconds=0.1,
        )
    )
    qa = QAAgent(
        "qa_1",
        db_session,
        test_runner=runner,
        contract_validator=ContractValidator(mock_llm),
    )
    out = await qa.review(
        uuid.uuid4(),
        "code",
        "def test_x(): pass",
        development_phase="BACKEND_PHASE",
        api_contract=_contract(),
        task_description="List tasks",
    )
    runner.run.assert_called_once()
    assert out.success is True


@pytest.mark.asyncio
async def test_violations_non_empty_when_invalid(mock_llm: AsyncMock) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(valid=False, violations=["a", "b"]),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    result = await ContractValidator(mock_llm).validate("x", _contract(), "task")
    assert result.violations


@pytest.mark.asyncio
async def test_runner_output_on_contract_violation(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(valid=False, violations=["schema mismatch"]),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    qa = QAAgent(
        "qa_1",
        db_session,
        test_runner=MagicMock(),
        contract_validator=ContractValidator(mock_llm),
    )
    out = await qa.review(
        uuid.uuid4(),
        "code",
        "tests",
        development_phase="BACKEND_PHASE",
        api_contract=_contract(),
        task_description="task",
    )
    assert out.failed_tests == 1
    assert out.stderr


@pytest.mark.asyncio
async def test_sandbox_error_contains_violation_description(
    db_session: AsyncSession,
    mock_llm: AsyncMock,
) -> None:
    mock_llm.complete.return_value = LLMResponse(
        content=_validation_json(valid=False, violations=["missing created_at"]),
        model_used="claude-haiku",
        input_tokens=10,
        output_tokens=10,
        estimated_cost_usd=0.0,
    )
    qa = QAAgent(
        "qa_1",
        db_session,
        test_runner=MagicMock(),
        contract_validator=ContractValidator(mock_llm),
    )
    out = await qa.review(
        uuid.uuid4(),
        "code",
        "tests",
        development_phase="BACKEND_PHASE",
        api_contract=_contract(),
        task_description="task",
    )
    assert out.sandbox_error is not None
    assert "API contract violation" in out.sandbox_error
    assert "created_at" in out.sandbox_error
