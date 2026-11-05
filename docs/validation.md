# Validation record — 2026-11-05

## Executed locally
- Python 3.13.5 / PyTorch 2.10.0+cpu / FastAPI 0.128.2, Linux x86_64.
- `OMP_NUM_THREADS=1 python -m pytest -q`: 39 passed, zero skipped.
- `node --check src/trace_llm/static/app.js`: passed.
- `python -m compileall -q src`: passed.
- `python -m pip wheel . --no-deps --no-build-isolation`: built wheel including static assets.
- Tests use artificial text only. No user diary or pretrained model was used.

Coverage includes exact parameter count, byte roundtrip, each-target-once windows,
causal attention, real loss reduction, optimizer/RNG continuation, optimistic edits,
immutable feed snapshots, idempotency, token caps, replay eligibility, masked-output
metadata, no auto-training, branch restore, queue/restore serialization, current
checkpoint retention, seven-day rollback after long inactivity, restart, interrupted
jobs, failed saves, nonfinite updates, tampered checkpoint detection and HTTP guards.

## Not verified
The real-browser test could not navigate to localhost in this container because
Chromium's administrative URL policy blocked navigation. Policy was not changed.
The Playwright browser download was also unavailable through container DNS.
`tests/browser_smoke.py` and a dedicated CI job are provided, but adding a test is
not evidence of a passing test. No screenshot-based layout approval is claimed.

GPU/Apple Silicon performance, other operating systems, genuine diary generalization,
and multi-user/public hosting are not validated. No speed or language-quality claim.

## Review
Manual source review, not an independent reviewer. Found/fixed: final short-window
input/target mismatch; SQLite WAL initialization inside a transaction; restoring while
another request enqueues training; and expiry of a stale current checkpoint immediately
after a new update. Regression tests cover these cases.
