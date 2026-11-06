# CPU Continual Learning Implementation Plan

> For agentic workers: execute inline with superpowers:executing-plans; each task RED→GREEN.

**Goal:** Improve CPU exposure control and measurable learning without breaking local stores.
**Architecture:** Existing byte RoPE GPT plus configurable token-budget trainer and evaluators.
**Tech Stack:** Python 3.11–3.13, PyTorch 2.10 CPU, SQLite, FastAPI, existing static JS.
**Spec:** docs/superpowers/specs/2026-11-06-cpu-learning.md

## Global Constraints
No external text or weights in production; no main writes; format 1/DB 1 additive only;
7-day rollback/current checkpoint protection; generated text excluded; explicit feeds only.

## Review Focus
Old checkpoint continuation; weighted gradients with ragged targets; exact target coverage;
repeat/edit mislabeled unseen; deleted or restored-away replay leakage; timing not hard quota.

### Task 1: Model/data/training primitives
Files: model.py, data.py, new training.py; tests/test_training.py.
Interfaces: TrainingConfig; backward_examples; windows(..., prefix=0); replay_window;
validate_tokenizer; model_config(preset, context).
- [x] Add failing primitive tests and run pytest.
- [x] Implement validated config, nonpersistent RoPE cache, overlap, replay and backward.
- [x] Verify full suite and commit.

### Task 2: Engine evaluation, budgets, persistence
Files: engine.py, new evaluation.py; tests/test_learning.py.
Interfaces: Engine(..., training=None); existing methods unchanged except optional
respond(..., checkpoint_id=None); evaluations() returns traceable job metrics.
- [x] Add failing tests for metrics, limits, legacy, resume/rollback and source exclusion.
- [x] Implement settings, token scheduler, separated backward, bounded probes and metadata.
- [x] Verify full suite and commit.

### Task 3: CLI/API/log integration
Files: app.py, static/app.js; tests/test_api.py, tests/test_learning.py.
Interfaces: create_app(..., config=None, training=None); /api/evaluations;
optional checkpoint_id in Generation; CLI preset/context/training flags for new stores.
- [x] Add failing API/settings tests.
- [x] Implement optional interfaces preserving existing UI and routes.
- [x] Run Python suite, JS syntax and browser smoke; commit.

### Task 4: Reproducible experiments and documentation
Files: benchmarks/cpu_study.py, docs/cpu-study.md, README.md.
- [x] Implement isolated fixture-only probes: sizes, contexts, threads, replay, LR,
      repetition, optimizers, ragged/padded, cold job and compile cost (bounded).
- [x] Record raw measurements, controls and limits; no benchmark fixtures loaded by app.
- [x] Review full diff, run full suite and package checks.
- [ ] Publish work branch and PR if connector permits; verify main unchanged.

Verification note: browser smoke was attempted but blocked by environment policy (ERR_BLOCKED_BY_ADMINISTRATOR); not passed locally.
