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


class SandboxProvisionError(ForgeAIError):
    """Raised when the sandbox container cannot be provisioned."""

    pass


class SandboxTimeoutError(ForgeAIError):
    """Raised when sandbox execution exceeds configured timeout."""

    pass


class AlreadyEscalatedError(ForgeAIError):
    """Raised when a level-5 task is retried without new human input."""

    pass


class CheckpointNotFoundError(ForgeAIError):
    """Raised when a checkpoint object does not exist in object storage."""

    pass


class LLMRateLimitError(ForgeAIError):
    """Raised when the Anthropic API rate-limits after retries are exhausted."""

    pass


class DuplicateComponentError(ForgeAIError):
    """Raised when registering a component name that already exists for the project."""

    pass


class BootstrapError(ForgeAIError):
    """Raised when the agent bootstrap protocol cannot complete."""

    pass


class ContextWindowExceededError(ForgeAIError):
    """Raised when context cannot be reduced below the model token limit."""

    pass
