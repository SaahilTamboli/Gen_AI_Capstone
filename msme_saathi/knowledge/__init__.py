"""Knowledge, Ingestion, Hybrid Retrieval, Deterministic Rule Engine, and Document Verification."""

from msme_saathi.knowledge.doc_checker import compare_document_fields, extract_document_fields
from msme_saathi.knowledge.hybrid_retriever import (
    CrossEncoderReranker,
    HybridPolicyRetriever,
    MultilingualDenseEmbedder,
    get_default_retriever,
    reciprocal_rank_fusion,
)
from msme_saathi.knowledge.ingestion import PolicyCorpusIngestor, audit_scheme_security, is_trusted_source_url
from msme_saathi.knowledge.models import (
    FlaggedSourceRecord,
    IngestionReport,
    PolicyClauseChunk,
    PolicyRuleSet,
    RuleCondition,
    SubsidyFormula,
)
from msme_saathi.knowledge.query_rewriter import normalize_digits, rewrite_query
from msme_saathi.knowledge.rule_engine import PolicyRuleEngine, get_default_rule_engine
from msme_saathi.knowledge.rule_extractor import evaluate_extractor_against_gold, extract_rules_from_scheme_clauses

__all__ = [
    "CrossEncoderReranker",
    "FlaggedSourceRecord",
    "HybridPolicyRetriever",
    "IngestionReport",
    "MultilingualDenseEmbedder",
    "PolicyClauseChunk",
    "PolicyCorpusIngestor",
    "PolicyRuleEngine",
    "PolicyRuleSet",
    "RuleCondition",
    "SubsidyFormula",
    "audit_scheme_security",
    "compare_document_fields",
    "evaluate_extractor_against_gold",
    "extract_document_fields",
    "extract_rules_from_scheme_clauses",
    "get_default_retriever",
    "get_default_rule_engine",
    "is_trusted_source_url",
    "normalize_digits",
    "reciprocal_rank_fusion",
    "rewrite_query",
]
