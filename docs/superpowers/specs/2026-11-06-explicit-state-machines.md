# TraceLLM explicit state machines — design specification

## Purpose
Make the local notebook and training control trustworthy under rapid user actions, background work, failures, and checkpoint branching. Preserve scratch CPU training, notebook-first workflow, masked responses, seven-day restores, SQLite v1, checkpoint format 1, and Windows/macOS/Linux browser UI. No external model or telemetry. This scope is a correctness and usability redesign, not a promise of language quality.

## Alternatives considered
1. **Documentation only:** draw state diagrams but keep implicit flags and SQL mutations. Cheapest, but races and invalid transitions remain.
2. **Explicit domain transitions + small UI reducer (selected):** replace dangerous implicit transitions with tested guards in Python, and centralize editor/navigation/presentation state in a small browser state module. Incremental, migration-free, testable.
3. **Complete application rewrite (e.g. React + new job framework):** more declarative UI but high migration risk and development cost; not justified for CPU-first single-user app.

## State model
### Notebook editor (per selected page)
- `empty` when no page selected; `clean` when loaded or saved; `dirty` when edited; `saving` while persisting; `conflict` for 409 revision mismatch; `error` for other save errors.
- `EDIT` clean/dirty/conflict/error -> dirty; `SAVE_START` dirty -> saving; `SAVE_SUCCESS` saving -> clean; `SAVE_CONFLICT` saving -> conflict; `SAVE_FAILURE` saving -> error; `RESET` -> clean (when an authoritative page is loaded); `CLOSE` -> empty.
- Conflict is non-destructive: retain text, prohibit navigation that would lose it until user resolves by copying or saving as a new page, or discards explicitly.
### UI navigation/request
- Orthogonal navigation `pages | health | history`; long action `idle | save | generate | feed | navigate | delete | restore`. Disabled controls reflect actual operation. Network read failures are visible; a stale load cannot replace a newer page or view.
- Response visibility `masked | revealed` bound to response selection; switching response always masks.
- Feed gesture moves a page version to `queued`; no immediate claim of training success. Keyboard and menu affordances remain.
### Durable training job
- `queued -> running -> succeeded | failed`; `queued -> cancelled`. No transitions out of terminal states.
- A deleted source page stops an in-progress training job before publication. Publish only if job is still running, source page is active, and parent checkpoint matches the current pointer, *inside the same transaction* as job completion. A failed publication cannot expose partially updated weights.
- Restart of `running` job -> `failed` with interruption record (no auto redo). An idempotent retry never creates a second job.
### Checkpoint
- `current | restorable | expired | unavailable`. Current never pruned. Restore creates new child checkpoint, does not mutate notebook pages. Existing file integrity/hash check remains.
### Health observations
- `loading | ready | stale | error`; quality `unknown` when independent evaluation absent. Never infer quality from training loss. Cross-branch data clearly labelled.

## UX redesign
- Primary screen is a page/notebook editor. Top bar includes clear local-only, model checkpoint, and operation status. A compact state strip next to editor communicates `保存済み/未保存/保存中/競合/エラー` and gives safe recovery.
- Learning state is discoverable from the sidebar and queue status. Successful feed is labelled *queued*, not *trained*. No gamified or fabricated growth score.
- Health view remains separate, retains measurable charts and missing data. Color/spacing/contrast, keyboard focus, desktop widths, and reduced motion improved without external runtime.
- New source files use bounded responsibilities; no database migration required.

## Security and compatibility
- Only localhost; no external requests. Escape user strings. Dataset remains repository-external. SQLite schema version 1 and checkpoint schema 1 remain unchanged. Newly created UI state never changes saved model files.
- No destructive overwrite on conflicting revision. Existing diary text remains in memory in the browser until safe recovery.

## Acceptance
1. Existing 99 Python tests pass unchanged.
2. New tests prove job transition matrix, stale/invalid publication rejection, orphan checkpoint cleanup, page-conflict recovery, UI state transitions, response masking and navigation fencing.
3. Python compile, JS syntax and Node tests pass. If possible run browser-level smoke and display screenshot; disclose blocked checks.
4. Source archive and patch are reviewable and clean, not directly merged into main.
