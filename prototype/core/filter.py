"""Filter — что из потока AgentEvent реально долетает до ClickUp.

См. docs/01_architecture_plan.md §8 (пример filter_config.yaml, обоснование
"никакого eval()") и docs/00_overview.md §4.2 (FilterDecision — не bool,
publish/publish_digest_only/drop через один интерфейс; MVP использует только
publish/drop, publish_digest_only зарезервирован под roadmap #3).

stage_changed вычисляется здесь (StageTracker), не читается из payload агента —
docs/07_grilled.md находка №1: агент не обязан и не должен знать, была ли смена
стадии "видимой" для человека, это забота ядра.
"""
from __future__ import annotations

import ast
import operator
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from core.events import AgentEvent, FilterContext, FilterDecision, Stage
from core.state_machine import APPROVAL_THRESHOLD_USD_DEFAULT

_COMPARATORS: dict[type, Any] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.LtE: operator.le,
}
# YAML/JSON-стиль литералов в filter_config.yaml (null/true/false), не Python
# (None/True/False) — конфиг пишет человек, не программист на Python.
_NAME_LITERALS = {"null": None, "true": True, "false": False}


def _safe_eval(expr: str, variables: dict[str, Any]) -> bool:
    """Безопасный интерпретатор сравнений — НЕ eval(). Разрешены только
    Compare/BoolOp/Name/Constant с операторами ==, !=, >, <, >=, <=, and, or,
    и именами из фиксированного словаря variables (+ null/true/false)."""
    tree = ast.parse(expr, mode="eval")
    return bool(_eval_node(tree.body, variables))


def _eval_node(node: ast.AST, variables: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_node(v, variables) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_eval_node(v, variables) for v in node.values)
        raise ValueError(f"filter_config: недопустимая булева операция {node.op!r}")
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op, comparator_node in zip(node.ops, node.comparators):
            fn = _COMPARATORS.get(type(op))
            if fn is None:
                raise ValueError(f"filter_config: недопустимый оператор {op!r}")
            right = _eval_node(comparator_node, variables)
            if not fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id in _NAME_LITERALS:
            return _NAME_LITERALS[node.id]
        if node.id not in variables:
            raise ValueError(f"filter_config: неизвестная переменная {node.id!r}")
        return variables[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    raise ValueError(f"filter_config: недопустимая конструкция {type(node).__name__}")


class StageTracker:
    """Ядро, не агент, решает stage_changed — см. docs/07_grilled.md находка №1."""

    def __init__(self) -> None:
        self._last_stage: dict[str, Stage] = {}

    def context_for(self, event: AgentEvent) -> FilterContext:
        changed = self._last_stage.get(event.task_id) != event.stage
        self._last_stage[event.task_id] = event.stage
        return FilterContext(event=event, stage_changed=changed)


FilterActionKind = Literal["publish", "publish_digest_only", "drop"]  # см. FilterDecision.action


class FilterRule(BaseModel):
    if_: str = Field(alias="if")
    action: FilterActionKind

    model_config = {"populate_by_name": True}


class FilterConfig(BaseModel):
    publish_rules: list[FilterRule] = []
    default_action: FilterActionKind = "drop"


class Filter:
    """MVP: только publish/drop правила (docs/00_overview.md §4.2) — интерфейс
    уже готов принять publish_digest_only, ядро при этом не меняется (roadmap #3)."""

    def __init__(self, config: FilterConfig, threshold_usd: float = APPROVAL_THRESHOLD_USD_DEFAULT) -> None:
        self._config = config
        self._threshold_usd = threshold_usd

    def decide(self, context: FilterContext) -> FilterDecision:
        variables = {
            "kind": context.event.kind,
            "requires_human": context.event.requires_human,
            "stage_changed": context.stage_changed,
            "cost_usd": context.event.cost_usd,
            "threshold_usd": self._threshold_usd,
        }
        for rule in self._config.publish_rules:
            if _safe_eval(rule.if_, variables):
                return FilterDecision(action=rule.action, reason=rule.if_)
        return FilterDecision(action=self._config.default_action, reason="default_action")


# Дефолт из docs/01_architecture_plan.md §8, дословно — используется, пока
# отдельный filter_config.yaml не подключён явно.
DEFAULT_FILTER_CONFIG = FilterConfig(
    publish_rules=[
        FilterRule(**{"if": "requires_human == true", "action": "publish"}),
        FilterRule(**{"if": "kind == 'flag'", "action": "publish"}),
        FilterRule(**{"if": "kind == 'report' and stage_changed == true", "action": "publish"}),
        FilterRule(**{"if": "cost_usd != null and cost_usd > threshold_usd", "action": "publish"}),
    ],
    default_action="drop",
)


def load_filter_config(path: str) -> FilterConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return FilterConfig.model_validate(raw)
