# Learning State and desktop runtime Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task; implementation explicitly requested.

**Goal:** Inspect learning without mistaking successful training for generalization, on desktop OSes.
**Architecture:** Read-only SQLite summary endpoint; separate vanilla JS view using real observations.
Existing FastAPI/CPU PyTorch remain the only runtimes. No schema/checkpoint change.
**Tech Stack:** Python, FastAPI, PyTorch, SQLite, browser JavaScript/CSS, pytest, Playwright.
**Spec:** docs/superpowers/specs/2026-11-06-learning-health-desktop.md

## Global Constraints
Local data only; no main edits; base b887d17 (PR #1, unmerged); explicit feed only; seven-day rollback.
No fabricated metrics, external assets, automatic schema/data migration, or claimed untested OS support.

## Review Focus
- Restored branch must not include discarded descendant training in learning metrics.
- Missing/legacy metrics and nonfinite values must not become zeros or a positive health label.
- Dashboard must not load model weights or block on the training lock.
- Native paths must not silently abandon a legacy diary store.
- Async view refresh must not overwrite current editing or show stale success after disconnect.

### Task 1: Read-only observations API
Files: health.py, app.py, tests/test_health.py. engine.py remains unchanged.
Interface: summarize_health(store, now=None) -> JSON-serializable dict; GET /api/learning-health.
- [ ] Write and run failing snapshot/branch/legacy/no-model-load/API tests.
- [ ] Implement read-only transactional aggregation; no model checkpoint loading.
- [ ] Run full pytest, commit.

### Task 2: Local runtime across desktop platforms
Files: runtime.py, __main__.py, app.py, tests/test_runtime.py, launchers, docs/desktop.md.
Interfaces: resolve_data_dir(...)->Path; run_local(app,port,open_browser=False).
- [ ] Write and run failing path/override/legacy/startup tests.
- [ ] Implement native paths with legacy preservation and readiness-checked browser opening.
- [ ] Add CPU install helper and launchers; verify subprocess/argument handling.
- [ ] Run full pytest and packaging checks, commit.

### Task 3: Learning State UI and desktop CI
Files: static/health.js, health.css, index.html, app.js; tests/browser_health.py; .github/workflows/ci.yml.
- [ ] Write and run browser checks for absent view (or record environment block).
- [ ] Add read-only cards, first-input observation dots, retention table, exposure/timing disclosures.
- [ ] Run browsers, review screenshot, inspect console/network, run full tests.
- [ ] Expand OS test matrix, package sources/evidence, publish separate stacked PR (never merge).
