# MSME-Incentive Saathi — One-Page Memory Policy

**Owner:** Tanmay (Team Member 2 — Knowledge, Memory & Tools Engineer)  
**Implementation:** `msme_saathi/memory/store.py` (`EnterpriseMemoryStore` + LangGraph `InMemoryStore` + SQLite) & `msme_saathi/memory/context_trimmer.py`

---

## 1. Storage Architecture & Pseudonymous Identity

- **Short-Term Memory:** Managed per conversation thread via LangGraph's `SqliteSaver` checkpointer (`msme_saathi/checkpointer.py`) and bounded in-context via `trim_or_summarize_messages()` (`max_recent_messages=6`, `max_tokens=1200`), which masks Aadhaar/account digits before summarizing older turns.
- **Long-Term Memory:** Stored in LangGraph `InMemoryStore` synchronized with persistent SQLite (`enterprise_memory` table) under namespace `("enterprises", PSEUDO-<16-char-HMAC-SHA256>)`. Raw enterprise IDs, Udyam numbers, GSTINs, PANs, and Aadhaar numbers are **never** used as storage keys.

---

## 2. Field-by-Field Justification (What Is Saved & Why)

| Stored Field | Type | Why This Field Is Stored |
|---|---|---|
| `state`, `district`, `taluka_zone` | `str` | Filters state schemes (e.g., Maharashtra PSI-2019) and backward industrial zone incentives (`C`, `D`, `D+`) without re-asking location on follow-up visits. |
| `sector` | `str` | Matches sector-specific capital grants (agro-processing, textiles, EV, precision engineering) across multi-scheme inquiries. |
| `enterprise_scale` | `Literal` | Determines statutory Micro / Small / Medium eligibility ceilings across Central and State schemes. |
| `plant_machinery_investment_lakhs` | `float` | Required by `rule-engine` to check investment caps (e.g., `<= Rs. 100L`) and calculate exact capital subsidy amounts in code. |
| `annual_turnover_lakhs` | `float` | Verifies MSME composite turnover thresholds where required by policy clauses. |
| `udyam_registered` | `bool` | Core statutory prerequisite across Ministry of MSME and Directorate of Industries schemes. |
| `commercial_production_date` | `str` | Verifies operative policy window eligibility (`YYYY-MM-DD`). |
| `project_type` | `Literal` | Distinguishes greenfield vs. expansion units (e.g., CMEGP / PMEGP / Stand-Up India). |
| `solar_upgrade_requested` | `bool` | Tracks clean-tech / rooftop solar intent for MEDA & SIDBI 4E green grants. |
| `past_applications` | `list[dict]` | Stores `tracking_id`, `policy_id`, `status`, and timestamp so returning entrepreneurs can ask *"What happened to my application?"* the following week. |

**Strictly Prohibited Fields (`FORBIDDEN_PII_KEYS`):** `aadhaar_number`, `pan_number`, `bank_account_number`, `ifsc`, `otp`, and `mobile_number` are automatically stripped on write and masked (`[MASKED-AADHAAR]`, `[MASKED-ACCOUNT]`) in text. ID numbers are passed only to `mock-portal.submit_application` at submission time.

---

## 3. When Data Is Saved & Retention Expiry

- **Explicit Opt-In Consent:** `save_profile()` and `record_application()` require `consent=True`. If `consent=False`, the store returns `CONSENT_REQUIRED` and writes zero bytes.
- **Bounded Retention (TTL):** Every record stores `created_at`, `updated_at`, and `expires_at` (default **90 days**). Any read at or after `expires_at` automatically purges the record from both SQLite and LangGraph `InMemoryStore` and returns `expired=True`.

---

## 4. Access Control, Correction & "Forget My Data" Deletion

- **Who Can Read:** Only the authenticated session matching the caller's `actor_enterprise_id` (and the assisting CSC/DIC operator within that session). Cross-tenant lookups (`actor_enterprise_id != target_enterprise_id`) are blocked with `TENANT_ACCESS_DENIED`.
- **How to Correct:** Entrepreneurs or DIC officers can update individual attributes via `correct_profile_fields()` or through the `edit` branch at the human `approval_gate`.
- **How to Delete ("Forget My Data"):** Calling `forget_me()` or sending *"Forget my data"*, *"मेरा डेटा हटा दें"* (Hindi), or *"माझा डेटा हटवा"* (Marathi) triggers immediate, irreversible cascade deletion of the profile and all past application history across SQLite and `InMemoryStore`.

---

## 5. Memory-On vs. Memory-Off Comparison (`python scripts/compare_memory.py`)

| Metric (Week-2 Return Visit Scenario) | Memory OFF | Memory ON (Consented) |
|---|---|---|
| Follow-up intake questions required on return visit | `1` (interrupts user again) | **`0`** (hydrates profile cleanly) |
| Reaches `approval_gate` draft directly without re-asking | `False` | **`True`** |
| Answers *"What happened to my application?"* (`TRK-...`) | `False` | **`True`** (`status="submitted"`) |
| Raw Aadhaar (`9999 8888 7777`) persisted to disk | N/A | **`Dropped`** (`dropped_pii_fields=["aadhaar_number"]`) |
| Record present after Marathi *"कृपया माझा डेटा हटवा"* | N/A | **`False`** (100% erased) |
