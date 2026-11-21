"""Typed LangGraph state for MSME-Incentive Saathi."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


LanguageCode = Literal["en", "hi", "mr"]
StepStatus = Literal["pending", "in_progress", "completed", "needs_replan", "skipped"]
PlanStage = Literal["profile", "candidate_schemes", "rules", "documents", "draft"]


class EnterpriseProfile(BaseModel):
    """Structured profile built by the Intake Agent and evaluated by the Rule Engine."""

    enterprise_id: str = "ENT-001"
    enterprise_name: str | None = None
    state: str | None = None
    district: str | None = None
    taluka_zone: Literal["A", "B", "C", "D", "D+"] | None = None
    sector: str | None = None
    enterprise_scale: Literal["micro", "small", "medium"] | None = None
    plant_machinery_investment_lakhs: float | None = None
    annual_turnover_lakhs: float | None = None
    udyam_registered: bool | None = None
    commercial_production_date: str | None = None  # YYYY-MM-DD
    project_type: Literal["greenfield", "expansion"] | None = None
    solar_upgrade_requested: bool | None = None
    uploaded_documents: list[dict[str, Any]] = Field(default_factory=list)


class PlanStep(BaseModel):
    """Single stage in the visible execution plan."""

    stage: PlanStage
    description: str
    status: StepStatus = "pending"


class ReplanRecord(BaseModel):
    """Audit note recorded whenever the Supervisor replans."""

    version: int
    trigger: str
    reason: str


class ExecutionPlan(BaseModel):
    """Visible plan shown to the entrepreneur / DIC officer."""

    version: int = 1
    steps: list[PlanStep] = Field(
        default_factory=lambda: [
            PlanStep(stage="profile", description="Build typed EnterpriseProfile"),
            PlanStep(stage="candidate_schemes", description="Search matching MSME policies"),
            PlanStep(stage="rules", description="Evaluate eligibility via Python rule-engine"),
            PlanStep(stage="documents", description="Verify checklist & document mismatches"),
            PlanStep(stage="draft", description="Draft application & get human approval"),
        ]
    )
    replans: list[ReplanRecord] = Field(default_factory=list)

    def set_status(self, stage: PlanStage, status: StepStatus) -> None:
        for step in self.steps:
            if step.stage == stage:
                step.status = status
                return


class MSMEState(TypedDict, total=False):
    """Persistent typed state stored in the LangGraph SQLite checkpointer."""

    messages: Annotated[list[AnyMessage], add_messages]
    language: LanguageCode
    user_query: str

    # Profile & intake tracking
    profile: EnterpriseProfile
    intake_done: bool
    intake_turns: int
    pending_missing_fields: list[str]
    questions_asked: list[str]

    # Planner & replanning
    plan: ExecutionPlan
    next_node: str
    search_broadened: bool
    replan_reason: str | None

    # Specialist outputs (plain JSON-compatible dicts mirroring MCP tool responses)
    eligibility_checked: bool
    candidate_schemes: list[dict[str, Any]]
    verdicts: dict[str, dict[str, Any]]
    document_report: dict[str, Any] | None
    selected_policy_id: str | None

    # Draft, approval gate & submission
    draft_application: dict[str, Any] | None
    approval_decision: dict[str, Any] | None
    submission_result: dict[str, Any] | None
    final_response: str
