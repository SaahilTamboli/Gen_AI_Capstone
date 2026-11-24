"""Document Checker logic: typed field extraction & cross-document consistency verification.

Handles synthetic MSME compliance documents (Udyam Certificate, GSTIN Certificate,
CA Investment Certificate, Machinery Invoices, Electricity Sanction Letter, Solar Quotation)
and flags name, GSTIN state-code, investment, and commencement-date mismatches with actionable fixes.
"""

from __future__ import annotations

import re
from typing import Any

_STATE_GST_CODES = {
    "27": "Maharashtra",
    "24": "Gujarat",
    "29": "Karnataka",
    "33": "Tamil Nadu",
    "09": "Uttar Pradesh",
}

_UDYAM_RE = re.compile(r"\b(UDYAM-[A-Z]{2}-\d{2}-\d{7})\b", re.I)
_GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b", re.I)
_PAN_RE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b", re.I)
_NAME_RE = re.compile(r"(?:enterprise\s*name|legal\s*name|trade\s*name|unit\s*name)\s*[:=-]\s*([^\n,;]+)", re.I)
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_INV_RE = re.compile(r"(?:investment|machinery\s*value|project\s*cost)\s*[:=-]?\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)\s*(?:lakhs?|l\b)", re.I)


def extract_document_fields(document_id: str, document_type: str, raw_text: str) -> dict[str, Any]:
    """Extract typed enterprise & statutory fields from synthetic MSME document text."""
    if not document_id or not document_type:
        return {
            "ok": False,
            "error": {"code": "INVALID_DOCUMENT_INPUT", "message": "document_id and document_type are required."},
        }

    extracted: dict[str, Any] = {
        "ok": True,
        "document_id": document_id,
        "document_type": document_type.strip().lower(),
        "raw_text": raw_text,
    }

    name_m = _NAME_RE.search(raw_text or "")
    if name_m:
        extracted["enterprise_name"] = name_m.group(1).strip()

    udyam_m = _UDYAM_RE.search(raw_text or "")
    if udyam_m:
        extracted["udyam_number"] = udyam_m.group(1).upper()

    gst_m = _GSTIN_RE.search(raw_text or "")
    if gst_m:
        gstin = gst_m.group(1).upper()
        extracted["gstin"] = gstin
        state_code = gstin[:2]
        if state_code in _STATE_GST_CODES:
            extracted["gst_state"] = _STATE_GST_CODES[state_code]

    pan_m = _PAN_RE.search(raw_text or "")
    if pan_m:
        # Mask middle characters in extracted summary to avoid raw PAN leakage in traces
        raw_pan = pan_m.group(1).upper()
        extracted["pan_masked"] = f"{raw_pan[:2]}*****{raw_pan[-2:]}"

    date_m = _DATE_RE.search(raw_text or "")
    if date_m:
        extracted["document_date"] = date_m.group(1)

    inv_m = _INV_RE.search(raw_text or "")
    if inv_m:
        extracted["investment_lakhs"] = float(inv_m.group(1))

    return extracted


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def compare_document_fields(
    profile: dict[str, Any],
    uploaded_docs: list[dict[str, Any]],
    required_docs: list[str],
) -> dict[str, Any]:
    """Compare uploaded MSME documents against required checklist and check cross-document consistency."""
    enriched_docs: list[dict[str, Any]] = []
    for idx, doc in enumerate(uploaded_docs or []):
        doc_copy = dict(doc)
        if doc_copy.get("raw_text") and "enterprise_name" not in doc_copy:
            parsed = extract_document_fields(
                document_id=str(doc_copy.get("document_id") or f"DOC-{idx + 1}"),
                document_type=str(doc_copy.get("document_type") or "unknown"),
                raw_text=str(doc_copy.get("raw_text", "")),
            )
            for k, v in parsed.items():
                if k not in doc_copy and k != "ok":
                    doc_copy[k] = v
        enriched_docs.append(doc_copy)

    present = {str(d.get("document_type")).strip() for d in enriched_docs if d.get("document_type")}
    missing = [doc for doc in (required_docs or []) if doc not in present]

    mismatches: list[dict[str, Any]] = []

    # 1. Cross-check legal enterprise name between Udyam Certificate and GSTIN Certificate
    names = {
        str(d.get("document_type")): str(d.get("enterprise_name")).strip()
        for d in enriched_docs
        if d.get("document_type") and d.get("enterprise_name")
    }
    if "udyam_certificate" in names and "gstin_certificate" in names:
        if _normalize_name(names["udyam_certificate"]) != _normalize_name(names["gstin_certificate"]):
            mismatches.append(
                {
                    "field": "enterprise_name",
                    "udyam_value": names["udyam_certificate"],
                    "gstin_value": names["gstin_certificate"],
                    "fix": "Amend Udyam or file GST REG-14 so enterprise names match identically.",
                }
            )

    # 2. Cross-check GSTIN state prefix vs EnterpriseProfile state
    profile_state = str(profile.get("state") or "").strip()
    for d in enriched_docs:
        if d.get("document_type") == "gstin_certificate" and d.get("gstin") and profile_state:
            prefix = str(d["gstin"])[:2]
            expected_state = _STATE_GST_CODES.get(prefix)
            if expected_state and expected_state.lower() != profile_state.lower():
                mismatches.append(
                    {
                        "field": "gstin_state_code",
                        "profile_state": profile_state,
                        "gstin_state": expected_state,
                        "fix": f"Provide the {profile_state} GSTIN registration certificate matching unit location.",
                    }
                )

    # 3. Cross-check CA Investment Certificate vs declared plant & machinery investment (>10% gap)
    declared_inv = profile.get("plant_machinery_investment_lakhs")
    for d in enriched_docs:
        if d.get("document_type") == "ca_investment_cert" and d.get("investment_lakhs") is not None and declared_inv is not None:
            ca_inv = float(d["investment_lakhs"])
            prof_inv = float(declared_inv)
            if abs(ca_inv - prof_inv) > max(0.10 * prof_inv, 1.0):
                mismatches.append(
                    {
                        "field": "plant_machinery_investment_lakhs",
                        "profile_value": prof_inv,
                        "ca_certificate_value": ca_inv,
                        "fix": "Reconcile plant & machinery figure with CA-certified fixed asset schedule and invoices.",
                    }
                )

    # 4. Cross-check Commercial Production Date discrepancy across Udyam / Electricity Sanction
    prof_date = profile.get("commercial_production_date")
    for d in enriched_docs:
        if d.get("commencement_date") and prof_date and str(d["commencement_date"]) != str(prof_date):
            mismatches.append(
                {
                    "field": "commercial_production_date",
                    "profile_value": prof_date,
                    "document_value": d["commencement_date"],
                    "document_type": d.get("document_type"),
                    "fix": "Update commercial production date on Udyam portal or attach DIC date-of-production certificate.",
                }
            )

    return {
        "ok": True,
        "present_documents": sorted(present),
        "missing_documents": missing,
        "mismatches": mismatches,
        "requires_human_referral": bool(mismatches),
    }
