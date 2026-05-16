"""Permitted state transitions and condition keys."""

from dataclasses import dataclass
from typing import Any

from forgeai.state_machine.states import TaskState


@dataclass(frozen=True)
class _TransitionDef:
    """Definition of a single allowed edge in the state graph."""

    from_state: TaskState
    to_state: TaskState
    requires_approval: bool = False
    requires_defect_report: bool = False
    requires_rework_reason: bool = False


# Every permitted edge; all other pairs are invalid.
_PERMITTED: tuple[_TransitionDef, ...] = (
    _TransitionDef(
        TaskState.PHASE_LOCKED,
        TaskState.TODO,
        requires_approval=True,
    ),
    _TransitionDef(TaskState.TODO, TaskState.IN_PROGRESS),
    _TransitionDef(TaskState.IN_PROGRESS, TaskState.IN_REVIEW),
    _TransitionDef(TaskState.IN_REVIEW, TaskState.TESTING),
    _TransitionDef(TaskState.TESTING, TaskState.DONE),
    _TransitionDef(
        TaskState.TESTING,
        TaskState.IN_PROGRESS,
        requires_defect_report=True,
    ),
    _TransitionDef(
        TaskState.DONE,
        TaskState.REWORK,
        requires_rework_reason=True,
    ),
    _TransitionDef(TaskState.REWORK, TaskState.IN_PROGRESS),
)

KEY_PHASE_APPROVAL = "phase_transition_approval"
KEY_DEFECT_REPORT = "defect_report"
KEY_REWORK_REASON = "rework_reason"
KEY_OUTPUT = "output"
KEY_WORK_OUTPUT = "work_output"
KEY_METADATA = "metadata"


def get_transition_def(
    from_state: TaskState, to_state: TaskState
) -> _TransitionDef | None:
    """Return the transition definition if the edge is permitted.

    Args:
        from_state: Current task state.
        to_state: Requested next state.

    Returns:
        The matching transition definition, or None if the edge is not allowed.
    """
    for t in _PERMITTED:
        if t.from_state == from_state and t.to_state == to_state:
            return t
    return None


def validate_conditions(
    tdef: _TransitionDef, kwargs: dict[str, Any]
) -> str | None:
    """Check transition-specific conditions; return error code or None if ok.

    Args:
        tdef: Permitted transition definition.
        kwargs: Arguments passed to the transition (e.g. approval flags, text).

    Returns:
        A short reason string if a condition failed, else None.
    """
    if tdef.requires_approval:
        if kwargs.get(KEY_PHASE_APPROVAL) is not True:
            return "phase_transition_approval required and must be True"
    if tdef.requires_defect_report:
        dr = kwargs.get(KEY_DEFECT_REPORT)
        if not isinstance(dr, str) or not dr.strip():
            return "defect_report must be a non-empty string"
    if tdef.requires_rework_reason:
        rr = kwargs.get(KEY_REWORK_REASON)
        if not isinstance(rr, str) or not rr.strip():
            return "rework_reason must be a non-empty string"
    return None
