"""Base mock agent."""

from sqlalchemy.ext.asyncio import AsyncSession


class BaseAgent:
    """Shared constructor storing agent identity and DB session."""

    def __init__(self, agent_id: str, db_session: AsyncSession) -> None:
        """Store agent id and async session for subclasses.

        Args:
            agent_id: Stable identifier for this agent instance.
            db_session: Active SQLAlchemy async session.
        """
        self.agent_id = agent_id
        self.db = db_session
