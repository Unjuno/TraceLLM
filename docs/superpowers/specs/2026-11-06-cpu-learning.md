# CPU-first continual learning redesign

## Contract
The user authorizes audit, design choice, implementation on a work branch, tests and
small artificial-data experiments. No main mutation; no diary/checkpoint publication.
Retain page UI, explicit feeds, frozen revisions, request idempotency, masked outputs,
branch-aware replay, format-1 checkpoints and SQLite version 1. CPU FP32 default.
No weights/tokenizer/corpus downloads or generated-text training. Test fixtures are
never initialised into a real user store.

## Audit and three approaches
1. Keep 115136-parameter RoPE byte GPT; improve data/exposure scheduling, observability
   and CPU execution. Lowest migration cost and most falsifiable; chosen.
2. Same family at 357216 / 820608 parameters, context 256/512/1024. More compute and
   capacity; no evidence of a personal-data benefit yet. Explicit fresh-store presets.
3. Character/BPE or hierarchical bytes. Fewer Japanese timesteps potentially, but vocabulary
   growth and optimiser/embedding migration or a second network are substantial debt.
   Research only; no forced migration.

## Changes
- Preserve default model shapes and state_dict keys. Cache deterministic RoPE trig buffers
  without persistence; reject invalid dimensions. Existing weights load strictly.
- Frozen TrainingConfig in checkpoint; missing settings in v0.1 get documented defaults.
  Explicit different settings on restart fail rather than silently resetting AdamW.
- Keep new-page target coverage once per requested pass; optional masked left overlap.
  Replay samples arbitrary target positions rather than always the prefix of a window.
- Token-normalised separate microbatch backward avoids padding a short replay to a long
  new window. Padded mode retained for fair experiment/compatibility; not assume faster.
- Requested effective targets drive work. max_steps and soft train-time budget are safety
  caps; report actual targets and stop reason. Optional cooperative duty cycle, not a
  hard OS CPU limit. Preserve serialized inference/training rather than add multiprocessing.
- Persist first-seen-page pretraining loss separately from repeat/edit losses. Exact hash
  equality is not near-duplicate detection; never claim independent held-out generalization.
- Fixed bounded old-page retention probes before/after each job, only already fed active
  branch pages; no saved-only/deleted pages. Old-data probes are not held-out validation.
- Record process CPU seconds, wall duration, exposures, corpus material bytes and policy.
  Unknown legacy cumulative exposure totals remain unknown, not falsely zero.
- Optional generation at a retained checkpoint for same-input comparison; original outputs
  immutable. Add small log metrics, not a UI redesign.
- Keep atomic publication, all numerical finite checks, recovery policy and 7-day rollback.
  Add tokenizer metadata as optional format-1 key; validate before continuation.

## Evaluation and boundaries
Three seeds for synthetic exploratory quality; repeated timing medians/ranges, hardware,
threads, precision, context, batch and non-pinned clocks recorded. Full dataset pages
held out temporally in probes; n-gram only an evaluation baseline. No text generation
claims from test-fixture losses. CPU-only container; no GPU/MPS/macOS/Windows claims.
No forced corpus migration, automatic model resizing or unlearning in this iteration.
