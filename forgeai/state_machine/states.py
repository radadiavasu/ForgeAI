"""Task state enumeration."""

from enum import Enum


class TaskState(str, Enum):
    """Valid task lifecycle states, in defined order."""

    PHASE_LOCKED = "PHASE_LOCKED"
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    TESTING = "TESTING"
    DONE = "DONE"
    REWORK = "REWORK"
