"""Deterministic Python Rule Engine over clause-linked JSON rules (`data/rules/scheme_rules.json`).

Enforces Capstone Rule #1:
  "The model proposes; code and people decide. Put arithmetic, eligibility rules, money and
   permissions in plain, tested code. The model selects the applicable rule and explains the result."
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from msme_saathi.knowledge.models import PolicyRuleSet, RuleCondition, SubsidyFormula

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "data" / "rules" / "scheme_rules.json"


class PolicyRuleEngine:
    """Evaluates typed `EnterpriseProfile` dicts against human-reviewed JSON scheme rules."""

    def __init__(self, rules_path: str | Path = DEFAULT_RULES_PATH) -> None:
        self.rules_path = Path(rules_path)
        payload = json.loads(self.rules_path.read_text(encoding="utf-8"))
        raw_rules: dict[str, Any] = payload.get("rules", {})
        self.rules_by_id: dict[str, PolicyRuleSet] = {
            pid: PolicyRuleSet.model_validate(spec) for pid, spec in raw_rules.items()
        }

    def list_policy_ids(self) -> list[str]:
        return sorted(self.rules_by_id.keys())

    def get_policy_rules(self, policy_id: str) -> dict[str, Any] | None:
        spec = self.rules_by_id.get(policy_id)
        return spec.model_dump() if spec else None

    def evaluate(self, profile: dict[str, Any], policy_id: str) -> dict[str, Any]:
        """Return `eligible`, `not_eligible`, or `cant_tell` with `deciding_clause` and exact INR subsidy."""
        spec = self.rules_by_id.get(policy_id)
        if spec is None:
            return {
                "ok": False,
                "policy_id": policy_id,
                "verdict": "cant_tell",
                "deciding_clause": f"{policy_id}/Unknown",
                "missing_fields": [],
                "estimated_subsidy_inr": None,
                "reason": f"Policy '{policy_id}' not found in reviewed rule-engine registry.",
                "error": {"code": "POLICY_NOT_FOUND", "policy_id": policy_id},
            }

        # 1. Check mandatory core fields first -> `cant_tell` if missing
        missing = [f for f in spec.required_fields if profile.get(f) is None]
        if missing:
            return {
                "ok": True,
                "policy_id": policy_id,
                "verdict": "cant_tell",
                "deciding_clause": spec.missing_field_clause,
                "missing_fields": missing,
                "estimated_subsidy_inr": None,
                "reason": f"Missing required fields: {', '.join(missing)}",
            }

        # 2. Evaluate deterministic conditions in order
        for cond in spec.conditions:
            passed = self._check_condition(profile, cond)
            if not passed:
                return {
                    "ok": True,
                    "policy_id": policy_id,
                    "verdict": "not_eligible",
                    "deciding_clause": cond.source_clause,
                    "missing_fields": [],
                    "estimated_subsidy_inr": 0.0,
                    "reason": cond.failure_reason or f"Failed condition on {cond.field} ({cond.operator} {cond.value}).",
                }

        # 3. Compute exact rupee subsidy in tested Python arithmetic
        subsidy_inr = self._compute_subsidy_inr(profile, spec.subsidy_formula)
        return {
            "ok": True,
            "policy_id": policy_id,
            "verdict": "eligible",
            "deciding_clause": spec.subsidy_formula.source_clause,
            "missing_fields": [],
            "estimated_subsidy_inr": subsidy_inr,
            "reason": spec.eligible_reason_template,
        }

    @staticmethod
    def _check_condition(profile: dict[str, Any], cond: RuleCondition) -> bool:
        actual = profile.get(cond.field)
        op = cond.operator
        target = cond.value

        if op == "not_null":
            return actual is not None

        if op == "in_or_none":
            if actual is None:
                return True
            return str(actual).lower() in {str(x).lower() for x in target}

        if op == "==":
            if isinstance(target, bool):
                return bool(actual) is target
            if actual is None:
                return False
            if isinstance(actual, str) and isinstance(target, str):
                return actual.strip().lower() == target.strip().lower()
            return actual == target

        if op == "!=":
            if actual is None:
                return True
            if isinstance(actual, str) and isinstance(target, str):
                return actual.strip().lower() != target.strip().lower()
            return actual != target

        if op == "in":
            if actual is None:
                return False
            return str(actual).strip().lower() in {str(x).strip().lower() for x in target}

        if op == "not_in":
            if actual is None:
                return True
            return str(actual).strip().lower() not in {str(x).strip().lower() for x in target}

        numeric_actual = float(actual or 0.0)
        numeric_target = float(target)
        if op == "<=":
            return numeric_actual <= numeric_target
        if op == ">=":
            return numeric_actual >= numeric_target
        if op == "<":
            return numeric_actual < numeric_target
        if op == ">":
            return numeric_actual > numeric_target
        return False

    @staticmethod
    def _compute_subsidy_inr(profile: dict[str, Any], formula: SubsidyFormula) -> float:
        if formula.type == "fixed_amount_inr":
            return round(float(formula.fixed_amount_inr), 2)

        inv_lakhs = float(profile.get(formula.investment_field) or 0.0)
        raw_inr = inv_lakhs * 100_000.0 * (formula.rate_pct / 100.0)
        return round(min(raw_inr, float(formula.cap_inr)), 2)


_DEFAULT_ENGINE: PolicyRuleEngine | None = None


def get_default_rule_engine() -> PolicyRuleEngine:
    """Singleton rule engine instance."""
    global _DEFAULT_ENGINE
    if _DEFAULT_ENGINE is None:
        _DEFAULT_ENGINE = PolicyRuleEngine()
    return _DEFAULT_ENGINE
