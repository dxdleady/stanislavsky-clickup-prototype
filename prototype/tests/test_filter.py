"""Юнит-тесты core/filter.py — каждое правило DEFAULT_FILTER_CONFIG отдельно,
default_action=drop, StageTracker, и что безопасный интерпретатор реально
отвергает недопустимые конструкции (не eval())."""
import pytest

from core.events import AgentEvent
from core.filter import DEFAULT_FILTER_CONFIG, Filter, FilterConfig, FilterRule, StageTracker, _safe_eval


def _event(**overrides) -> AgentEvent:
    base = dict(
        kind="report", agent_id="a", task_id="t", stage="in_progress",
        correlation_id="corr_1", event_seq=1,
    )
    base.update(overrides)
    return AgentEvent(**base)


def test_stage_tracker_first_event_is_changed():
    tracker = StageTracker()
    ctx = tracker.context_for(_event(stage="backlog"))
    assert ctx.stage_changed is True


def test_stage_tracker_same_stage_is_not_changed():
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t1", stage="in_progress"))
    assert ctx.stage_changed is False


def test_stage_tracker_different_stage_is_changed():
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t1", stage="done"))
    assert ctx.stage_changed is True


def test_stage_tracker_tracks_per_task():
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t2", stage="in_progress"))
    assert ctx.stage_changed is True  # другая задача — независимая история


@pytest.fixture
def filter_() -> Filter:
    return Filter(DEFAULT_FILTER_CONFIG, threshold_usd=15.0)


def test_requires_human_publishes(filter_):
    tracker = StageTracker()
    ctx = tracker.context_for(_event(requires_human=True, stage_changed=False))
    decision = filter_.decide(ctx)
    assert decision.action == "publish"


def test_flag_kind_publishes_even_without_stage_change(filter_):
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", kind="flag", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t1", kind="flag", stage="in_progress"))
    decision = filter_.decide(ctx)
    assert decision.action == "publish"


def test_report_with_stage_change_publishes(filter_):
    tracker = StageTracker()
    ctx = tracker.context_for(_event(kind="report", stage="planned"))  # первое событие — stage_changed=True
    decision = filter_.decide(ctx)
    assert decision.action == "publish"


def test_report_without_stage_change_drops_by_default(filter_):
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", kind="report", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t1", kind="report", stage="in_progress"))
    decision = filter_.decide(ctx)
    assert decision.action == "drop"
    assert decision.reason == "default_action"


def test_cost_above_threshold_publishes(filter_):
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t1", stage="in_progress", cost_usd=20.0))
    decision = filter_.decide(ctx)
    assert decision.action == "publish"


def test_cost_below_threshold_does_not_match_cost_rule(filter_):
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t1", stage="in_progress", cost_usd=5.0))
    decision = filter_.decide(ctx)
    assert decision.action == "drop"


def test_cost_none_does_not_crash_on_null_comparison(filter_):
    tracker = StageTracker()
    tracker.context_for(_event(task_id="t1", stage="in_progress"))
    ctx = tracker.context_for(_event(task_id="t1", stage="in_progress", cost_usd=None))
    decision = filter_.decide(ctx)
    assert decision.action == "drop"


# --- безопасный интерпретатор — не eval() ---


def test_safe_eval_rejects_function_calls():
    with pytest.raises(ValueError):
        _safe_eval("__import__('os').system('echo pwned')", {})


def test_safe_eval_rejects_attribute_access():
    with pytest.raises(ValueError):
        _safe_eval("kind.__class__", {"kind": "report"})


def test_safe_eval_rejects_unknown_variable():
    with pytest.raises(ValueError):
        _safe_eval("nonexistent == true", {})


def test_safe_eval_and_or():
    assert _safe_eval("a == 1 and b == 2", {"a": 1, "b": 2}) is True
    assert _safe_eval("a == 1 and b == 3", {"a": 1, "b": 2}) is False
    assert _safe_eval("a == 1 or b == 3", {"a": 1, "b": 2}) is True
