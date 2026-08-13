"""Машина состояний — конфигурируемая per-workflow (per List/домен), не единый enum.

Найдено при разборе docs/source/stanislavsky_costume_demo_dataset_v2.json
(docs/08_dataset_analysis.md находка №1): домен "костюмный цех" использует
10 статусов ClickUp, полностью отличных от 7-стадийной модели из 01_tz.md.
Значит Stage — не фиксированный Literal, а значение, легальность и поведение
которого задаёт WorkflowConfig. Два workflow ниже (CAMERA_PIPELINE,
COSTUME_PIPELINE) — не гипотеза "можно расширить", а рабочее доказательство,
что архитектура выдерживает второй реальный домен без правки этого модуля.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

RouterBranch = Literal["cheap_edit", "needs_regeneration"]
SilenceAction = Literal["pause", "apply_agent_recommendation", "repeat_escalation"]


class EscalationPolicy(BaseModel):
    """Политика молчания (P6/R6, ТЗ) — числа подтверждены datasets/*_v2.json escalation_policies."""

    timeout_min: int
    action: SilenceAction
    escalate_to: list[str] = []
    repeat: bool = False


class StageDefinition(BaseModel):
    id: str
    name: str
    waits_for_human: bool = False
    # Активна ли задача на этой стадии для Router (docs/00_overview.md §4.3,
    # docs/07_grilled.md находка №2 — Deferred/Blocked активны, не "закрыты").
    active_for_routing: bool = True
    # None у стадий, ожидающих человека — вопрос "нужна ли регенерация" там
    # не имеет смысла, это сама точка решения (docs/07_grilled.md находка №4).
    rework_branch: RouterBranch | None = None
    escalation: EscalationPolicy | None = None


class WorkflowConfig(BaseModel):
    """Флоу расширяется конфигурацией, а не переписыванием (ТЗ, нефункц. требования)."""

    id: str
    stages: dict[str, StageDefinition]
    transitions: dict[str, list[str]]  # from stage id -> допустимые to stage id
    closed_stages: set[str]  # комментарий сюда → новый тикет, не доработка (P3, ветка 2)

    @model_validator(mode="after")
    def _validate_refs(self) -> "WorkflowConfig":
        unknown_transition_stages = set(self.transitions) | {s for v in self.transitions.values() for s in v}
        if not unknown_transition_stages <= set(self.stages):
            raise ValueError(f"{self.id}: transitions ссылаются на неизвестные стадии")
        if not self.closed_stages <= set(self.stages):
            raise ValueError(f"{self.id}: closed_stages ссылаются на неизвестные стадии")
        return self

    def is_valid_transition(self, frm: str, to: str) -> bool:
        if frm not in self.stages or to not in self.stages:
            return False
        return to in self.transitions.get(frm, [])

    def classify_branch(self, stage: str) -> RouterBranch:
        definition = self.stages[stage]
        if definition.rework_branch is None:
            raise ValueError(
                f"{self.id}/{stage}: rework-классификация не определена "
                "(это стадия ожидания человека — вопрос branch здесь не имеет смысла)"
            )
        return definition.rework_branch

    def escalation_for(self, stage: str) -> EscalationPolicy | None:
        return self.stages[stage].escalation

    def is_active_for_routing(self, stage: str) -> bool:
        return self.stages[stage].active_for_routing


# --- Дефолтная стоимость/порог approve, см. docs/01_architecture_plan.md §3 ---
# Порог и валюта — per-workspace/per-task конфигурация (custom field cost_threshold,
# docs/03_clickup_requirements.md §3), это ЗНАЧЕНИЕ — просто дефолт-фоллбек для camera-домена
# ($15 из демо-трейса деки). costume-датасет использует ₽50000 — другой домен, другой дефолт,
# ровно то же самое поле в другой конфигурации, не противоречие.
APPROVAL_THRESHOLD_USD_DEFAULT = 15.0

# Фоллбек, если у стадии нет своего EscalationPolicy (стадии без waits_for_human
# в escalation вообще не нуждаются — это только для decision_request без своей политики).
DEFAULT_DECISION_DEADLINE_MINUTES = 30


# ============================================================================
# CAMERA_PIPELINE — исходная 7-стадийная модель из 01_tz.md §3 / 02_architecture.md §3
# ============================================================================

CAMERA_PIPELINE = WorkflowConfig(
    id="camera_pipeline",
    stages={
        "backlog": StageDefinition(id="backlog", name="Backlog", rework_branch="cheap_edit"),
        "planned": StageDefinition(id="planned", name="Planned", rework_branch="cheap_edit"),
        "in_progress": StageDefinition(
            id="in_progress", name="In Progress", rework_branch="needs_regeneration"
        ),
        "ready_for_verification": StageDefinition(
            id="ready_for_verification", name="Ready for Verification", rework_branch="needs_regeneration"
        ),
        "verification": StageDefinition(
            id="verification", name="Verification", rework_branch="needs_regeneration"
        ),
        "done": StageDefinition(id="done", name="Done", active_for_routing=False),
        "deferred": StageDefinition(
            id="deferred", name="Deferred", waits_for_human=True,
            escalation=EscalationPolicy(timeout_min=DEFAULT_DECISION_DEADLINE_MINUTES, action="pause"),
        ),
    },
    transitions={
        "backlog": ["planned", "deferred"],
        "planned": ["in_progress", "deferred"],
        "in_progress": ["ready_for_verification", "deferred"],
        "ready_for_verification": ["verification", "deferred"],
        "verification": ["done", "in_progress", "deferred"],  # in_progress == Regenerate
        "deferred": ["backlog", "planned", "in_progress", "ready_for_verification", "verification"],
    },
    closed_stages={"done"},
)


# ============================================================================
# COSTUME_PIPELINE — из docs/source/stanislavsky_costume_demo_dataset_v2.json
# statuses[] + escalation_policies[], transitions выведены из tasks[].state_history
# и events[].status_change по обоим датасетам (см. docs/08_dataset_analysis.md находка №1)
# ============================================================================

COSTUME_PIPELINE = WorkflowConfig(
    id="costume_pipeline",
    stages={
        "backlog": StageDefinition(id="backlog", name="Backlog", rework_branch="cheap_edit"),
        "ready": StageDefinition(id="ready", name="Ready for Director", rework_branch="cheap_edit"),
        "wardrobe_check": StageDefinition(
            id="wardrobe_check", name="Wardrobe Check", rework_branch="needs_regeneration"
        ),
        "coverage_review": StageDefinition(
            id="coverage_review", name="Coverage Review", waits_for_human=True,
            escalation=EscalationPolicy(timeout_min=30, action="apply_agent_recommendation", escalate_to=["supervisor"]),
        ),
        "cost_approval": StageDefinition(
            id="cost_approval", name="Cost Approval", waits_for_human=True,
            escalation=EscalationPolicy(timeout_min=30, action="pause", escalate_to=["timur"]),
        ),
        "in_generation": StageDefinition(
            id="in_generation", name="In Generation", rework_branch="needs_regeneration"
        ),
        "qc": StageDefinition(id="qc", name="QC", rework_branch="needs_regeneration"),
        "regenerate": StageDefinition(
            id="regenerate", name="Regenerate", waits_for_human=True,
            escalation=EscalationPolicy(timeout_min=20, action="apply_agent_recommendation", escalate_to=["supervisor"]),
        ),
        "accepted": StageDefinition(id="accepted", name="Accepted", active_for_routing=False),
        "blocked": StageDefinition(
            id="blocked", name="Blocked", waits_for_human=True,
            escalation=EscalationPolicy(
                timeout_min=240, action="repeat_escalation",
                escalate_to=["producer", "first_ad"], repeat=True,
            ),
        ),
    },
    transitions={
        "backlog": ["ready", "cost_approval", "in_generation"],
        "ready": ["cost_approval", "wardrobe_check", "in_generation", "blocked"],
        "blocked": ["ready"],
        "cost_approval": ["ready", "wardrobe_check", "in_generation"],
        "wardrobe_check": ["in_generation"],
        "in_generation": ["qc"],
        "qc": ["accepted", "regenerate"],
        "regenerate": ["in_generation"],
    },
    closed_stages={"accepted"},
)
