# Explicit state machines / UX reliability Implementation Plan

> **For agentic workers:** Execute tasks in TDD order. Each task must have real regression tests before implementation.

**Goal:** Make TraceLLM's hidden editor, job, checkpoint, and learning health states explicit, safe, and inspectable without destroying saved diaries.
**Architecture:** Preserve local FastAPI, SQLite and scratch decoder GPT; isolate business transitions in small pure modules, validate all commit conditions in the database transaction, then wire browser state to a pure reducer and improve the editor's recovery UI.
**Tech Stack:** Python 3.11–3.13, PyTorch 2.10, FastAPI, SQLite, vanilla JS/CSS, Node built-in test runner.
**Spec:** docs/superpowers/specs/2026-11-06-explicit-state-machines.md

## Global constraints
- CPU default, no external model or telemetry.
- Preserve notebook editor, masked generation, swipe feed, 7-day branches.
- Schema 1 and checkpoint format 1 preserved. Never edit main.
- Test-first, verify full suite after each completed task.

## Review focus
- Deleted source during training publication must not be committed.
- Checkpoint IO or DB transaction error must not leave active pointer referencing partial output.
- Conflict while switching pages must preserve unsaved note text.
- Slow navigation response must not overwrite newer selection.
- UI generation and mask state must not be confused with trained state.

### Task 1: Durable job transition guards
- Tests in `tests/test_state_transitions.py` establish legal/illegal paths.
- Add `src/trace_llm/lifecycle.py` for pure transition validation and explicit atomic conditional transition helper.
- Use it in queue cancellation/claim and train commit paths. Check source active/parent pointer in same transaction.
- Tests include forced delete-before-publish and cleanup of unreferenced checkpoint after failed publication.

### Task 2: Pure UI state machine
- JS test `tests/ui_machine.test.cjs` with Node built-in `node --test` exercises editor transitions, operation busy, navigation and response visibility.
- Add `static/ui_machine.js`, load before `app.js`; integrate existing handlers with state transitions.
- Guard against stale async page reads; preserve drafts on conflict.

### Task 3: User-facing recovery, status, and accessibility
- DOM tests for new recovery UI and actual button state where possible, browser smoke checks.
- Add conflict banner actions (save as new, reload with explicit confirmation) and live operation/queue status.
- Improve desktop focus states, responsive layout, controls. Keep health metrics truthful.

### Task 4: Verification and handoff
- Full Python+Node+browser tests, compare archive against branch, self-review, record limitations.
- ZIP source + patch + verification JSON. If GitHub write possible, push new branch and PR (do not touch main).
