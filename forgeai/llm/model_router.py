"""Model router — complexity and loop_count → model id (Req 30)."""

from __future__ import annotations

from forgeai.llm.schemas import ModelPool


class ModelRouter:
    """Maps task complexity and escalation loop to a concrete model id.

    Stateless: no memory, no LLM calls, no project context.
    """

    def __init__(self, model_pool: ModelPool) -> None:
        self.pool = model_pool

    def route(self, complexity: str, loop_count: int = 0) -> str:
        """Return model identifier for the given complexity tier and loop count.

        Routing rules (Req 30):

        - If ``loop_count`` < 2: tier ``default`` model.
        - If ``loop_count`` >= 2: tier ``escalated`` model.
        - Tier routing is strict: LOW uses only ``pool.low``, MEDIUM only
          ``pool.medium``, HIGH only ``pool.high`` (no cross-tier jumps).

        Args:
            complexity: ``LOW``, ``MEDIUM``, or ``HIGH``.
            loop_count: Current loop counter for this task.

        Returns:
            Model identifier string, e.g. ``claude-sonnet-4-6``.

        Raises:
            ValueError: If ``complexity`` is not recognized.
        """
        tier = complexity.strip().upper()
        pool_map = {
            "LOW": self.pool.low,
            "MEDIUM": self.pool.medium,
            "HIGH": self.pool.high,
        }
        if tier not in pool_map:
            raise ValueError(f"Invalid complexity: {complexity!r}")
        chosen = pool_map[tier]
        return chosen.default if loop_count < 2 else chosen.escalated

    def get_tier_ceiling(self, complexity: str) -> str:
        """Maximum model id available within this tier (escalated slot).

        LOW → ``MODEL_LOW_ESCALATED`` pool value; same pattern for MEDIUM/HIGH.
        """
        tier = complexity.strip().upper()
        pool_map = {
            "LOW": self.pool.low,
            "MEDIUM": self.pool.medium,
            "HIGH": self.pool.high,
        }
        if tier not in pool_map:
            raise ValueError(f"Invalid complexity: {complexity!r}")
        return pool_map[tier].escalated
