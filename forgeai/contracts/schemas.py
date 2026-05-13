"""Navigation contract, layout specification, and component registry schemas (Phase 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class RouteDefinition(BaseModel):
    path: str
    owner_agent_id: str
    component_name: str
    is_root_layout: bool = False


class NavigationContract(BaseModel):
    version: str = "1.0"
    project_id: str
    routes: list[RouteDefinition]
    shared_layout_component: str = "AppLayout"
    shared_layout_owner: str
    linking_convention: str = 'react-router-dom Link component'
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_by: str = "lead_agent"


class PageSpec(BaseModel):
    name: str
    route: str
    sections: list[str] = Field(default_factory=list)
    interactions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class SharedComponentSpec(BaseModel):
    name: str
    used_by_pages: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    description: str = ""


class LayoutSpecification(BaseModel):
    project_id: str
    source: str  # "mockup" | "architect_generated"
    pages: list[PageSpec]
    shared_components: list[SharedComponentSpec] = Field(default_factory=list)
    design_tokens: dict[str, Any] = Field(default_factory=dict)


class ComponentEntry(BaseModel):
    component_name: str
    owner_agent_id: str
    interface_definition: str
    file_path: str
    project_id: str
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    used_by: list[str] = Field(default_factory=list)


class FrontendOutput(BaseModel):
    code: str
    test_code: str
    components_registered: list[str] = Field(default_factory=list)
    components_imported: list[str] = Field(default_factory=list)
    file_path: str = "src/pages/Component.jsx"
