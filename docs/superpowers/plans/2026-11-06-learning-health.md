# Learning Health Implementation Plan

> For agentic workers: execute task-by-task using executing-plans and TDD.

**Goal:** Inspect local learning without inventing quality claims, on three desktop OSes.
**Architecture:** Read-only SQLite health view plus existing FastAPI and standalone JS/SVG.
**Tech Stack:** Python, FastAPI, SQLite, browser standards, pytest and Playwright.
**Spec:** docs/superpowers/specs/2026-11-06-learning-health.md

## Global constraints
CPU default; user-fed text only; no external runtime calls; seven-day restore; no
main mutation; DB/checkpoint format1 unchanged; old store paths remain usable.

## Review focus
- Restore lineage must not count abandoned descendants.
- Unknown legacy metrics, NaN and missing values must never look healthy or zero.
- Refresh must not block training or overwrite edits / mask choices / filters.
- Narrow desktop, keyboard and server error states must remain operable.
- Windows/macOS path choices and CI must not accidentally create a new empty store.

## Task 1 — health data contract
Files: src/trace_llm/health.py (new), app.py, tests/test_health.py (new).
Interface: health_snapshot(engine, *, scope='current', days=7, limit=120, now=None).
- [x] Test nonexistent endpoint fails 404, then initial snapshot succeeds.
- [x] Add tests for real updates, repeated vs edited, legacy/nonfinite, lineage,
  period/truncation, counts, no content, failure and read-only operation.
- [x] Implement single snapshot projection, attach GET route with bounded query validation.
- [x] Run full pytest suite and commit.

## Task 2 — health view
Files: static/index.html, app.js, health.js (new), health.css (new),
 tests/browser_health.py (new), tests/test_health.py.
Interface: window.TraceHealth.create({api,toast,action}) -> show/refresh/hide.
- [x] Add browser workflow test (missing nav fails).
- [x] Add stable view, filters, empty state, scatter plots, table+detail, local export,
  two-checkpoint masked compare; integrate without changing page behavior.
- [x] Verify component render with a real browser and TestClient bridge, screenshots and full test suite; commit.
- [ ] Real HTTP/CSP browser integration blocked locally by administrator policy; configured in CI, not run.

## Task 3 — desktop support
Files: paths.py, __main__.py, app.py, tests/test_paths.py, CI, README.
- [x] Test platform defaults, legacy detection and explicit precedence fail.
- [x] Implement path resolution, module entry, portability docs and 3-OS CI.
- [x] Run tests and build/install wheel in isolated target; commit.
- [ ] Real HTTP browser smoke: attempted and blocked; not passed. Windows/macOS execution not available locally.

## Task 4 — verify and publish
- [x] Separate self-review including failed jobs, rollback, keyboard and stale refresh.
- [x] Full suite; component/browser interaction regression; package check; retain logs and screenshots. HTTP/CSP E2E remains blocked as above.
- [x] Verify remote base and prepare an apply-checked patch and source archive.
- [ ] Publish new work branch / stacked PR / inspect new CI: unavailable with the read-only GitHub connector. No remote write was attempted.
- [x] Report only verified tests/platforms and limitations; no merge to main.
