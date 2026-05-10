"""Pydantic schemas for LLM requests, router pool, and structured agent outputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TierPool(BaseModel):
    """Models for a complexity tier at default vs escalated loop counts."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"default": "claude-haiku-4-5-20251001", "escalated": "claude-sonnet-4-6"}
        }
    )

    default: str = Field(
        ...,
        description="Model when loop_count < 2",
        examples=["claude-haiku-4-5-20251001"],
    )
    escalated: str = Field(
        ...,
        description="Model when loop_count >= 2",
        examples=["claude-sonnet-4-6"],
    )


class ModelPool(BaseModel):
    """Full six-model pool loaded from environment (Req 30)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "low": {"default": "claude-haiku-4-5-20251001", "escalated": "claude-sonnet-4-6"},
                "medium": {"default": "claude-sonnet-4-6", "escalated": "claude-sonnet-4-6"},
                "high": {"default": "claude-sonnet-4-6", "escalated": "claude-opus-4-6"},
            }
        }
    )

    low: TierPool
    medium: TierPool
    high: TierPool

    @classmethod
    def from_env(cls) -> ModelPool:
        """Load all six model strings from ``Settings`` environment variables."""
        from forgeai.config import get_settings

        s = get_settings()
        return cls(
            low=TierPool(default=s.pool_low_default, escalated=s.pool_low_escalated),
            medium=TierPool(default=s.pool_medium_default, escalated=s.pool_medium_escalated),
            high=TierPool(default=s.pool_high_default, escalated=s.pool_high_escalated),
        )


class LLMResponse(BaseModel):
    """Normalized response from ``LLMClient.complete``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": '{"ok": true}',
                "model_used": "claude-sonnet-4-6",
                "input_tokens": 100,
                "output_tokens": 50,
                "estimated_cost_usd": 0.00105,
                "tool_calls": [],
            }
        }
    )

    content: str = Field(..., description="Primary text from the assistant", examples=['{"a": 1}'])
    model_used: str = Field(..., examples=["claude-sonnet-4-6"])
    input_tokens: int = Field(..., ge=0, examples=[500])
    output_tokens: int = Field(..., ge=0, examples=[200])
    estimated_cost_usd: float = Field(..., examples=[0.0045])
    tool_calls: list[Any] = Field(
        default_factory=list,
        description="Raw tool-use payloads when tools were invoked",
        examples=[[{"type": "tool_use", "name": "web_search", "id": "x"}]],
    )


# --- Research / Architect structured outputs ---


class TechnologyOption(BaseModel):
    """One evaluated technology option from Research_Agent."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "FastAPI",
                "pros": ["Async", "OpenAPI"],
                "cons": ["Smaller ecosystem than Django"],
                "suitable": True,
            }
        }
    )

    name: str = Field(..., examples=["FastAPI"])
    pros: list[str] = Field(default_factory=list, examples=[["Async native", "OpenAPI"]])
    cons: list[str] = Field(default_factory=list, examples=[["Less batteries included"]])
    suitable: bool = Field(..., examples=[True])


class TechStack(BaseModel):
    """Recommended stack summary shared by research and architecture outputs."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "language": "Python",
                "framework": "FastAPI",
                "database": "PostgreSQL",
                "testing_framework": "pytest",
                "rationale": "Fits team skills and deployment constraints",
                "rejected_alternatives": ["Django"],
            }
        }
    )

    language: str = Field(..., examples=["Python"])
    framework: str = Field(..., examples=["FastAPI"])
    database: str = Field(..., examples=["PostgreSQL"])
    testing_framework: str = Field(..., examples=["pytest"])
    rationale: str = Field(..., examples=["Matches constraints and ops profile"])
    rejected_alternatives: list[str] = Field(
        default_factory=list,
        examples=[["Django", "Node.js"]],
    )


class ResearchOutput(BaseModel):
    """Structured research findings for Architect_Agent."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "domain_summary": "Restaurant reservations",
                "technology_options": [
                    {
                        "name": "FastAPI",
                        "pros": ["Fast"],
                        "cons": [],
                        "suitable": True,
                    }
                ],
                "recommended_stack": {
                    "language": "Python",
                    "framework": "FastAPI",
                    "database": "PostgreSQL",
                    "testing_framework": "pytest",
                    "rationale": "Fit",
                    "rejected_alternatives": [],
                },
                "constraints_respected": ["Python"],
                "research_sources": ["https://example.com"],
            }
        }
    )

    domain_summary: str = Field(..., examples=["Online restaurant booking domain"])
    technology_options: list[TechnologyOption] = Field(default_factory=list)
    recommended_stack: TechStack
    constraints_respected: list[str] = Field(
        default_factory=list,
        examples=[["Python", "PostgreSQL"]],
    )
    research_sources: list[str] = Field(
        default_factory=list,
        examples=[["https://docs.python.org"]],
    )


class Component(BaseModel):
    """Architect-defined software component."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "API",
                "responsibility": "HTTP surface",
                "dependencies": ["DB"],
                "acceptance_criteria": ["OpenAPI present"],
            }
        }
    )

    name: str = Field(..., examples=["Reservation API"])
    responsibility: str = Field(..., examples=["Expose REST endpoints"])
    dependencies: list[str] = Field(default_factory=list, examples=[["PostgreSQL"]])
    acceptance_criteria: list[str] = Field(default_factory=list, examples=[["Returns 201 on book"]])


class DataField(BaseModel):
    """Field within a logical data model."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "id",
                "type": "uuid",
                "required": True,
                "description": "Primary key",
            }
        }
    )

    name: str = Field(..., examples=["id"])
    type: str = Field(..., examples=["uuid"])
    required: bool = Field(..., examples=[True])
    description: str = Field(..., examples=["Primary key"])


class DataModel(BaseModel):
    """Logical data model specification."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Reservation",
                "fields": [
                    {
                        "name": "id",
                        "type": "uuid",
                        "required": True,
                        "description": "PK",
                    }
                ],
            }
        }
    )

    name: str = Field(..., examples=["Reservation"])
    fields: list[DataField] = Field(default_factory=list)


class APISurface(BaseModel):
    """HTTP API contract slice."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "endpoint": "/menu",
                "method": "GET",
                "request_schema": {},
                "response_schema": {"items": "array"},
                "description": "List menu items",
            }
        }
    )

    endpoint: str = Field(..., examples=["/reservations"])
    method: str = Field(..., examples=["POST"])
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(..., examples=["Create reservation"])


class MasterDocument(BaseModel):
    """Authoritative project specification (Req 14)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "version": "1.0",
                "project_name": "Demo",
                "project_summary": "Summary",
                "components": [],
                "data_models": [],
                "api_surfaces": [],
                "tech_stack": {
                    "language": "Python",
                    "framework": "FastAPI",
                    "database": "PostgreSQL",
                    "testing_framework": "pytest",
                    "rationale": "Fit",
                    "rejected_alternatives": [],
                },
                "constraints": [],
                "created_at": "2026-05-10T12:00:00+00:00",
            }
        }
    )

    version: str = Field(default="1.0", examples=["1.0"])
    project_name: str = Field(..., examples=["Restaurant Booking"])
    project_summary: str = Field(..., examples=["Full-stack booking site"])
    components: list[Component] = Field(default_factory=list)
    data_models: list[DataModel] = Field(default_factory=list)
    api_surfaces: list[APISurface] = Field(default_factory=list)
    tech_stack: TechStack
    constraints: list[str] = Field(default_factory=list, examples=[["Docker deploy"]])
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TechStackDocument(BaseModel):
    """Formal technology stack document derived from research."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "language": "Python",
                "framework": "FastAPI",
                "database": "PostgreSQL",
                "testing_framework": "pytest",
                "libraries": ["uvicorn"],
                "rationale": "Aligned with constraints",
                "rejected_alternatives": [],
                "version": "1.0",
                "created_at": "2026-05-10T12:00:00+00:00",
            }
        }
    )

    language: str = Field(..., examples=["Python"])
    framework: str = Field(..., examples=["FastAPI"])
    database: str = Field(..., examples=["PostgreSQL"])
    testing_framework: str = Field(..., examples=["pytest"])
    libraries: list[str] = Field(default_factory=list, examples=[["httpx", "pytest"]])
    rationale: str = Field(..., examples=["Matches deployment and team"])
    rejected_alternatives: list[str] = Field(default_factory=list, examples=[["Django"]])
    version: str = Field(default="1.0", examples=["1.0"])
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
