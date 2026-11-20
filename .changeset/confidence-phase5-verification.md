---
"@doc-intelligence/server": minor
"@doc-intelligence/agent": minor
---

Phase 5: Verification, Data Sources Entity Matching & Dual-Pass Self-Correction

- Implemented `resolve_entity_matches` in `server/app/services/identity_check.py` to evaluate extracted entities against master knowledge data and knowledge sources
- Wired data sources scoring in `record_service.py` and `/records/validate` endpoint
- Added dual-pass verification (+5 pts visual bonus) and threshold boundary self-correction guidance in `confidence_service.py`, `records.py`, and `agentic_framework`
- Added comprehensive pytest integration test suite `server/tests/test_confidence_phase5_integration.py` (73/73 tests pass)
