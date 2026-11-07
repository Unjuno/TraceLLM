# Learning health UI / desktop portability

## Intent
A dedicated, local-only place to inspect whether training executes safely, whether
first-seen text prediction changes, and whether prior trained text is retained.
Do not equate training fit, first-seen prediction and independently held-out quality.
Keep page editor, swipe feeding, masked generations, CPU default and seven-day restore.

## Chosen approach
User delegated framework choice and requested immediate implementation. Keep FastAPI,
SQLite and existing browser frontend. Add isolated vanilla JS/SVG health components;
no CDN, telemetry, frontend build or network dependency at runtime. A new React app
would duplicate state/build tools; Electron/Tauri would add a desktop runtime and
packaging surface without helping CPU learning. OS native executables are not in scope.

Target current Windows, macOS and Linux desktop browsers. CI must test Python+browser
on all three OS families, with Windows/Linux CPU torch wheels and native macOS torch.
Keep Python 3.11-3.13 and torch 2.10 contract. Do not claim every hardware/OS version.

## Data contract
Read-only GET /api/learning-health?scope=current|all&days=7|30|0&limit=120.
Read a single SQLite snapshot, do not load weights or acquire the training lock. Follow
checkpoint parent_id (not previous_id) to choose current lineage, including pruned
checkpoint metadata. Full counts/totals cover selected period/scope, while chart/table
rows are the latest limit records; clearly disclose truncation. UI default current/7d.
Only succeeded, published updates contribute to quality charts and exposure totals.
Failed/queued/running/cancelled jobs remain visible as operational history. Pending
jobs are global because their starting checkpoint is selected at execution time.
Missing/legacy/nonfinite metrics are null, never zero or a fabricated score. Retention
is a paired change on previously trained probes within one job, not held-out accuracy.
First-seen classification is exact-hash/page identity, not independence. Independent
validation remains explicitly unavailable. Timings exclude publication, CPU seconds
are accumulated process time (not a utilization percent). Tokens include EOS.

## UI
Add left-nav 学習の健全性 with filters, visible last-refresh/offline state, operational
summary, first-seen sample count, recorded new/replay exposures, checkpoint candidates.
Show first-seen loss scatter (different pages, no trend/score), retention signed scatter
(zero baseline, different probes between jobs), and exact-value job table with detail.
Display no-data, legacy-data, failure, truncated, deletion invalidation and partial-job
states explicitly. Export local metric JSON without note body or page title. Offer
same-page/same-revision two-checkpoint comparison, greedy fixed settings, masked by
default, no automatic training. Reuse existing APIs; never alter note or model on read.

## Desktop lifecycle
Keep legacy data locations when an existing DB is present; use platform-specific
new-store defaults with explicit path/env precedence. Add python -m trace_llm entry.
Provide Windows/macOS/Linux setup, offline-runtime and backup notes. Do not migrate
DBs or models, silently install dependencies or open the network interface.

## Safety and validation
TDD on metric handling, lineage/restore, missing/invalid values, period/counts, read-only
snapshot during training, original API controls, legacy paths. Browser tests cover
empty/data/failure/filter/export/comparison/keyboard and 1024/1440 desktop widths.
Real training in disposable stores only. Keep GitHub main/PR1 untouched: new branch
based on PR1, stacked PR against PR1. No independent reviewer tool is available;
perform a separate documented self-review and disclose that limitation.
