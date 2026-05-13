"""Intra-frontend coordination: navigation contract and component registry (Phase 6)."""

from forgeai.contracts.navigation import NavigationNegotiator
from forgeai.contracts.registry import ComponentRegistry
from forgeai.contracts.schemas import (
    ComponentEntry,
    FrontendOutput,
    LayoutSpecification,
    NavigationContract,
    PageSpec,
    RouteDefinition,
    SharedComponentSpec,
)

__all__ = [
    "ComponentEntry",
    "ComponentRegistry",
    "FrontendOutput",
    "LayoutSpecification",
    "NavigationContract",
    "NavigationNegotiator",
    "PageSpec",
    "RouteDefinition",
    "SharedComponentSpec",
]
