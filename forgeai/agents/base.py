"""Base mock agent."""

from sqlalchemy.ext.asyncio import AsyncSession

from forgeai.memory.task_memory import TaskMemory


class BaseAgent:
    """Shared constructor storing agent identity and DB session."""

    def __init__(
        self,
        agent_id: str,
        db_session: AsyncSession,
        *,
        task_memory: TaskMemory | None = None,
    ) -> None:
        """Store agent id and async session for subclasses.

        Args:
            agent_id: Stable identifier for this agent instance.
            db_session: Active SQLAlchemy async session.
            task_memory: Optional Redis-backed task memory for DONE cleanup.
        """
        self.agent_id = agent_id
        self.db = db_session
        self.task_memory = task_memory
