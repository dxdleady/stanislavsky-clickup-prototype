"""Все 7 сценариев ТЗ как обычные pytest-тесты — тот же run(), что и в
sim/runner.py (docs/04_test_plan.md: "sim/scenarios импортируются в
tests/test_scenarios.py")."""
from sim.scenarios import (
    s01_happy_path,
    s02_reply_to_current_agent,
    s03_reply_to_previous_iteration,
    s04_batched_comments,
    s05_out_of_context_noise,
    s06_cost_threshold,
    s07_clickup_outage,
)


def test_01_happy_path():
    s01_happy_path.run()


def test_02_reply_to_current_agent():
    s02_reply_to_current_agent.run()


def test_03_reply_to_previous_iteration():
    s03_reply_to_previous_iteration.run()


def test_04_batched_comments():
    s04_batched_comments.run()


def test_05_out_of_context_noise():
    s05_out_of_context_noise.run()


def test_06_cost_threshold():
    s06_cost_threshold.run()


def test_07_clickup_outage():
    s07_clickup_outage.run()
