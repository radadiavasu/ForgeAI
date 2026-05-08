"""Unit tests for LoopCounter behavior."""

from forgeai.escalation.loop_counter import LoopCounter


def test_increment_returns_correct_count() -> None:
    counter = LoopCounter()
    assert counter.increment("task-1", "sandbox_timeout") == 1
    assert counter.increment("task-1", "sandbox_timeout") == 2


def test_get_returns_zero_for_unseen_combination() -> None:
    counter = LoopCounter()
    assert counter.get("unknown-task", "schema_violation") == 0


def test_should_escalate_false_below_threshold() -> None:
    counter = LoopCounter()
    counter.increment("task-1", "output_missing")
    counter.increment("task-1", "output_missing")
    assert counter.should_escalate("task-1", "output_missing") is False


def test_should_escalate_true_at_three() -> None:
    counter = LoopCounter()
    for _ in range(3):
        counter.increment("task-1", "output_missing")
    assert counter.should_escalate("task-1", "output_missing") is True


def test_reset_clears_all_task_counters() -> None:
    counter = LoopCounter()
    counter.increment("task-1", "output_missing")
    counter.increment("task-1", "sandbox_timeout")
    counter.reset("task-1")
    assert counter.get("task-1", "output_missing") == 0
    assert counter.get("task-1", "sandbox_timeout") == 0


def test_reset_does_not_affect_other_tasks() -> None:
    counter = LoopCounter()
    counter.increment("task-1", "output_missing")
    counter.increment("task-2", "output_missing")
    counter.reset("task-1")
    assert counter.get("task-1", "output_missing") == 0
    assert counter.get("task-2", "output_missing") == 1


def test_signatures_tracked_independently_per_task() -> None:
    counter = LoopCounter()
    counter.increment("task-1", "output_missing")
    counter.increment("task-1", "sandbox_timeout")
    counter.increment("task-1", "sandbox_timeout")
    assert counter.get("task-1", "output_missing") == 1
    assert counter.get("task-1", "sandbox_timeout") == 2
