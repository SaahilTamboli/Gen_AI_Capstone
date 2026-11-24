"""Typed Pydantic data models for MSME policy ingestion, retrieval, and deterministic rules."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class PolicyClause(BaseModel):
    """Single numbered clause within an official MSME scheme notification."""

    clause_id: str
    section: str
    title: str
    text: str


class PolicyClauseChunk(BaseModel):
    """Ingested clause chunk indexed in BM25 and Chroma vector store with full provenance."""

    chunk_id: str
    policy_id: str
    scheme_name: str
    authority: str
    state: str
    clause_id: str
    primary_clause_id: str
    section: str
    title: str
    text: str
    snippet: str
    source_url: str
    collection_date: str
    validity_date: str
    is_official_source: bool = True
    eligible_scales: list[str] = Field(default_factory=list)
    taluka_zones: list[str] = Field(default_factory=list)
    target_sectors: list[str] = Field(default_factory=list)
    requires_solar: bool = False
    keywords_en: list[str] = Field(default_factory=list)
    keywords_hi: list[str] = Field(default_factory=list)
    keywords_mr: list[str] = Field(default_factory=list)


class FlaggedSourceRecord(BaseModel):
    """Audit record for untrusted, scam, or prompt-injection documents caught during ingestion/retrieval."""

    policy_id: str
    source_url: str
    reasons: list[str]


class IngestionReport(BaseModel):
    """Summary returned after running the policy ingestion pipeline."""

    total_schemes_loaded: int
    official_schemes_indexed: int
    total_clause_chunks: int
    flagged_sources: list[FlaggedSourceRecord] = Field(default_factory=list)


RuleOperator = Literal["==", "!=", "in", "in_or_none", "not_in", "<=", ">=", "<", ">", "not_null"]


class RuleCondition(BaseModel):
    """Single clause-grounded deterministic condition evaluated by `rule-engine`."""

    field: str
    operator: RuleOperator
    value: Any
    source_clause: str
    failure_reason: str = ""


class SubsidyFormula(BaseModel):
    """Deterministic Python subsidy calculation parameters."""

    type: Literal["percentage_of_investment_lakhs_with_cap", "fixed_amount_inr"] = (
        "percentage_of_investment_lakhs_with_cap"
    )
    investment_field: str = "plant_machinery_investment_lakhs"
    rate_pct: float = 0.0
    cap_inr: float = 0.0
    fixed_amount_inr: float = 0.0
    source_clause: str


class PolicyRuleSet(BaseModel):
    """Reviewed JSON rule specification for one MSME scheme."""

    policy_id: str
    scheme_name: str
    gold_standard: bool = False
    human_reviewed: bool = True
    reviewed_by: str = "Role 2"
    review_date: str
    required_fields: list[str] = Field(default_factory=list)
    missing_field_clause: str
    conditions: list[RuleCondition] = Field(default_factory=list)
    subsidy_formula: SubsidyFormula
    eligible_reason_template: str
