"""Corpus ingestion pipeline: validates provenance/dates, enforces official sources, and chunks by clause."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from msme_saathi.knowledge.models import FlaggedSourceRecord, IngestionReport, PolicyClauseChunk

DEFAULT_CORPUS_PATH = Path(__file__).resolve().parents[2] / "data" / "schemes" / "msme_schemes_corpus.json"
DEFAULT_SCAM_PATH = Path(__file__).resolve().parents[2] / "data" / "schemes" / "untrusted_scam_pages.json"

TRUSTED_OFFICIAL_DOMAINS = (
    ".gov.in",
    ".nic.in",
    "mahaurja.com",
    "midcindia.org",
    "cgtmse.in",
    "sidbi.in",
    "standupmitra.in",
    "scsthub.in",
    "udyamimitra.in",
)

_SCAM_OR_INJECTION_PATTERNS = [
    re.compile(r"\bupi\s*id\b", re.I),
    re.compile(r"processing\s+fee", re.I),
    re.compile(r"system\s+instruction\s*:", re.I),
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|rule[- ]engine)", re.I),
    re.compile(r"aadhaar\s+otp", re.I),
]


def is_trusted_source_url(url: str) -> bool:
    """Return True only if `url` uses HTTPS and belongs to an allow-listed official government/nodal domain."""
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    return any(host == dom.lstrip(".") or host.endswith(dom) for dom in TRUSTED_OFFICIAL_DOMAINS)


def audit_scheme_security(raw_scheme: dict[str, Any]) -> list[str]:
    """Inspect a raw scheme entry for untrusted origin, missing metadata, scam fees, or prompt injection."""
    reasons: list[str] = []
    source_url = str(raw_scheme.get("source_url") or "")
    if not raw_scheme.get("is_official_source", True) or not is_trusted_source_url(source_url):
        reasons.append(f"untrusted_domain:{source_url or 'missing'}")
    if not raw_scheme.get("collection_date"):
        reasons.append("missing_collection_date")
    if not raw_scheme.get("validity_date"):
        reasons.append("missing_validity_date")

    combined_text = " ".join(
        [
            str(raw_scheme.get("scheme_name", "")),
            str(raw_scheme.get("snippet", "")),
            *(str(c.get("text", "")) for c in raw_scheme.get("clauses", [])),
        ]
    )
    for pat in _SCAM_OR_INJECTION_PATTERNS:
        if pat.search(combined_text):
            reasons.append(f"suspicious_content:{pat.pattern}")
    return reasons


class PolicyCorpusIngestor:
    """Loads MSME scheme JSON documents, enforces security/metadata checks, and produces clause chunks."""

    def __init__(
        self,
        corpus_path: str | Path = DEFAULT_CORPUS_PATH,
        include_untrusted_for_audit: bool = False,
        untrusted_path: str | Path = DEFAULT_SCAM_PATH,
    ) -> None:
        self.corpus_path = Path(corpus_path)
        self.include_untrusted_for_audit = include_untrusted_for_audit
        self.untrusted_path = Path(untrusted_path)
        self.schemes_by_id: dict[str, dict[str, Any]] = {}
        self.flagged_by_id: dict[str, FlaggedSourceRecord] = {}
        self.chunks: list[PolicyClauseChunk] = []
        self.untrusted_chunks: list[PolicyClauseChunk] = []

    def ingest(self) -> IngestionReport:
        payload = json.loads(self.corpus_path.read_text(encoding="utf-8"))
        raw_schemes: list[dict[str, Any]] = list(payload.get("schemes", []))

        if self.include_untrusted_for_audit and self.untrusted_path.exists():
            scam_payload = json.loads(self.untrusted_path.read_text(encoding="utf-8"))
            raw_schemes.extend(scam_payload.get("scam_pages", []))

        self.schemes_by_id.clear()
        self.flagged_by_id.clear()
        self.chunks.clear()
        self.untrusted_chunks.clear()

        for scheme in raw_schemes:
            pid = scheme["policy_id"]
            audit_issues = audit_scheme_security(scheme)
            built_chunks = self._scheme_to_chunks(scheme, is_official=(len(audit_issues) == 0))

            if audit_issues:
                self.flagged_by_id[pid] = FlaggedSourceRecord(
                    policy_id=pid,
                    source_url=str(scheme.get("source_url", "")),
                    reasons=audit_issues,
                )
                self.untrusted_chunks.extend(built_chunks)
                continue

            self.schemes_by_id[pid] = scheme
            self.chunks.extend(built_chunks)

        return IngestionReport(
            total_schemes_loaded=len(raw_schemes),
            official_schemes_indexed=len(self.schemes_by_id),
            total_clause_chunks=len(self.chunks),
            flagged_sources=list(self.flagged_by_id.values()),
        )

    @staticmethod
    def _scheme_to_chunks(scheme: dict[str, Any], is_official: bool) -> list[PolicyClauseChunk]:
        pid = scheme["policy_id"]
        kw = scheme.get("keywords") or {}
        primary_clause_id = scheme.get("primary_clause_id") or scheme["clauses"][0]["clause_id"]
        out: list[PolicyClauseChunk] = []
        for idx, cl in enumerate(scheme.get("clauses", [])):
            out.append(
                PolicyClauseChunk(
                    chunk_id=f"{pid}::{idx}::{cl['clause_id']}",
                    policy_id=pid,
                    scheme_name=scheme["scheme_name"],
                    authority=scheme.get("authority", "Official Nodal Agency"),
                    state=scheme["state"],
                    clause_id=cl["clause_id"],
                    primary_clause_id=primary_clause_id,
                    section=cl.get("section", ""),
                    title=cl.get("title", scheme["scheme_name"]),
                    text=cl["text"],
                    snippet=scheme.get("snippet", cl["text"][:200]),
                    source_url=scheme["source_url"],
                    collection_date=scheme["collection_date"],
                    validity_date=scheme["validity_date"],
                    is_official_source=is_official,
                    eligible_scales=list(scheme.get("eligible_scales", [])),
                    taluka_zones=list(scheme.get("taluka_zones", [])),
                    target_sectors=list(scheme.get("target_sectors", [])),
                    requires_solar=bool(scheme.get("requires_solar", False)),
                    keywords_en=list(kw.get("en", [])),
                    keywords_hi=list(kw.get("hi", [])),
                    keywords_mr=list(kw.get("mr", [])),
                )
            )
        return out
