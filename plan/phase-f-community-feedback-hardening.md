# Phase F - Community Feedback Hardening

Status: Planned

## Goal

Address trust and realism issues identified in early Reddit feedback without sacrificing determinism, reproducibility, or bracket-rule correctness.

## Scope

Core prediction stack:
- `src/world_ranking.py`
- `src/engine.py`

Data pipeline:
- `scripts/parse_fifa_men_ranking.py`
- `data/fifa_men_ranking_static.json` generation/refresh workflow

Presentation layer:
- `src/ui.py`
- `src/api.py` response metadata extensions (if needed)

Reference feedback:
- `feedback/reddit-feedback-2026-05-27.md`

## Tasks

1. Ranking freshness and provenance:
   - Guarantee latest FIFA ranking snapshot selection in ingestion.
   - Surface ranking date/data staleness in UI and API responses.
2. Probability calibration pass:
   - Review/retune group and knockout win/draw transforms.
   - Compare predicted frequencies against baseline expectations and public market sanity checks.
3. Beam and truncation sensitivity:
   - Add diagnostic runs for different beam widths/min branch probability.
   - Choose defaults that preserve realistic tail outcomes.
4. Third-place allocation correctness:
   - Replace deterministic per-world assignment shortcut with full allowed-combination handling.
   - Ensure probability mass is allocated across all valid mappings.
5. User-facing trust improvements:
   - Add short "How this prediction is generated" section.
   - Add explicit caveats for uncertainty and model limits.
6. Regression protection:
   - Add tests for controversial groups/outcomes (e.g., Group D, Group F).
   - Add checks that probabilities remain normalized and non-degenerate.

## Acceptance Criteria

1. Prediction payload includes transparent data provenance (ranking date/source).
2. Group-level outputs no longer collapse implausibly to near-single-team certainty for debated groups without strong evidence.
3. Third-place bracket mapping is rules-complete and test-covered.
4. Tests pass and include new guardrails for previously reported issues.
5. UI copy clearly communicates uncertainty and limitations.

## Rollout Notes

1. Run side-by-side comparison of old vs new outputs for selected match numbers (80, 81, 82, 84, 93, 94).
2. Publish changelog summary using plain language for community follow-up.
3. Keep deterministic fallback path available for debugging and offline usage.

