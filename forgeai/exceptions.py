"""Application-wide exceptions for ForgeAI."""


class ForgeAIError(Exception):
    """Base exception for ForgeAI domain errors."""

    pass


class InvalidTransitionError(ForgeAIError):
    """Raised when the requested from→to pair is not in the permitted map."""

    pass


class TransitionConditionError(ForgeAIError):
    """Raised when a permitted transition fails its condition checks."""

    pass


class SelfApprovalError(ForgeAIError):
    """Raised when QA attempts to act on work produced by the same agent id."""

    pass
