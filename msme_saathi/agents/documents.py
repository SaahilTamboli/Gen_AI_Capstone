"""Document Checker: pulls required checklists and flags field mismatches."""

from __future__ import annotations

from typing import Any

from msme_saathi.state import EnterpriseProfile, MSMEState
from msme_saathi.tools import compare_document_fields, get_document_checklist


def document_checker_node(state: MSMEState) -> dict[str, Any]:
    """Verify uploaded documents against checklists for all eligible schemes."""
    profile = state.get("profile") or EnterpriseProfile()
    verdicts = state.get("verdicts", {})

    eligible_ids = [pid for pid, v in verdicts.items() if v.get("verdict") == "eligible"]
    required_docs = sorted(
        {
            doc
            for pid in eligible_ids
            for doc in get_document_checklist(pid).get("required_documents", [])
        }
    )

    report = compare_document_fields(
        profile=profile.model_dump(),
        uploaded_docs=profile.uploaded_documents,
        required_docs=required_docs,
    )
    return {"document_report": report}
