"""Typed Pydantic schemas, structured error envelopes, and READ/WRITE tool access matrix for FastMCP servers."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

ToolAccessMode = Literal["read", "write"]


class StructuredToolError(BaseModel):
    """Structured error returned instead of raising uncaught server exceptions."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PolicySearchInput(BaseModel):
    query: str = Field(..., description="Entrepreneur / DIC search query in English, Hindi, or Marathi")
    state: str | None = Field(default=None, description="State name filter, e.g. 'Maharashtra'")
    district_tier: str | None = Field(default=None, description="Industrial taluka zone: A, B, C, D, or D+")
    enterprise_scale: str | None = Field(default=None, description="MSME scale: micro, small, or medium")
    sector: str | None = Field(default=None, description="Manufacturing sector slug")
    broadened: bool = Field(default=False, description="If True, include pan-India Central MSME schemes")


class PolicyCandidateOutput(BaseModel):
    policy_id: str
    scheme_name: str
    authority: str = "Official Nodal Agency"
    state: str
    clause_id: str
    matched_passage_id: str | None = None
    validity_date: str
    collection_date: str = "2026-10-24"
    source_url: str = ""
    snippet: str
    score: float = 0.0


class PolicyClauseInput(BaseModel):
    policy_id: str = Field(..., description="Unique policy identifier, e.g. 'MH-PSI-2019-CAP'")
    clause_id: str = Field(..., description="Exact clause citation ID, e.g. 'MH-PSI-2019/Ch.4/Cl.4.2'")


class EvaluateEligibilityInput(BaseModel):
    profile: dict[str, Any] = Field(..., description="Serialized EnterpriseProfile dictionary")
    policy_id: str = Field(..., description="Target MSME scheme policy_id")


class EvaluateEligibilityOutput(BaseModel):
    ok: bool = True
    policy_id: str
    verdict: Literal["eligible", "not_eligible", "cant_tell"]
    deciding_clause: str
    missing_fields: list[str] = Field(default_factory=list)
    estimated_subsidy_inr: float | None = None
    reason: str
    error: StructuredToolError | None = None


class ExtractDocumentInput(BaseModel):
    document_id: str
    document_type: str
    raw_text: str


class CompareDocumentsInput(BaseModel):
    profile: dict[str, Any]
    uploaded_docs: list[dict[str, Any]]
    required_docs: list[str]


class CreateDraftInput(BaseModel):
    enterprise_id: str
    policy_id: str
    profile: dict[str, Any]
    revision: int = 1


class SubmitApplicationInput(BaseModel):
    draft_id: str
    payload: dict[str, Any]
    approval_token: str | None = None


# Canonical Read/Write Tool Permission Catalog (Capstone Minimum Bar: mark every tool read/write,
# grant write access ONLY to the application submission agent).
TOOL_PERMISSION_CATALOG: dict[str, dict[str, Any]] = {
    "search_policies": {
        "server": "msme-policies-kb",
        "access": "read",
        "allowed_agents": ["eligibility_analyst", "single_agent_baseline"],
        "description": "Hybrid BM25 + Chroma RRF + Reranker policy clause search across EN/HI/MR.",
    },
    "get_policy_clause": {
        "server": "msme-policies-kb",
        "access": "read",
        "allowed_agents": ["eligibility_analyst", "response_synthesizer", "single_agent_baseline"],
        "description": "Retrieve exact numbered clause text, official URL, collection date, and validity date.",
    },
    "get_document_checklist": {
        "server": "msme-policies-kb",
        "access": "read",
        "allowed_agents": ["document_checker", "single_agent_baseline"],
        "description": "Fetch mandatory compliance document checklist for a given policy_id.",
    },
    "evaluate_eligibility": {
        "server": "rule-engine",
        "access": "read",
        "allowed_agents": ["eligibility_analyst", "single_agent_baseline"],
        "description": "Deterministic Python rule evaluation returning verdict, deciding_clause, and exact INR grant.",
    },
    "get_policy_rules": {
        "server": "rule-engine",
        "access": "read",
        "allowed_agents": ["eligibility_analyst", "single_agent_baseline"],
        "description": "Inspect reviewed JSON rule conditions and subsidy formula for audit transparency.",
    },
    "extract_document_fields": {
        "server": "doc-check",
        "access": "read",
        "allowed_agents": ["document_checker", "intake_agent", "single_agent_baseline"],
        "description": "Extract structured Udyam, GSTIN, masked PAN, investment, and date fields from synthetic text.",
    },
    "compare_document_fields": {
        "server": "doc-check",
        "access": "read",
        "allowed_agents": ["document_checker", "single_agent_baseline"],
        "description": "Cross-check uploaded compliance documents against profile & checklist and flag mismatches.",
    },
    "create_application_draft": {
        "server": "mock-portal",
        "access": "read",
        "allowed_agents": ["application_draft", "application_agent", "single_agent_baseline"],
        "description": "Prepare non-binding portal form draft with canonical SHA-256 payload digest.",
    },
    "get_application_status": {
        "server": "mock-portal",
        "access": "read",
        "allowed_agents": ["intake_agent", "application_draft", "application_agent", "response_synthesizer", "single_agent_baseline"],
        "description": "Check submitted subsidy application status by tracking_id.",
    },
    "submit_application": {
        "server": "mock-portal",
        "access": "write",
        "allowed_agents": ["application_submit", "application_agent"],
        "description": "[WRITE — APPROVAL GATE REQUIRED] Commit approved subsidy application to Portal DB.",
    },
}
