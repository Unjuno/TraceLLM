# TraceLLM local-first MVP

Approved product direction: one page is one editable note, not a chat thread.
A page swipe snapshots the saved revision and queues training. Saving or generating
never trains. Outputs are hidden by default and retain their page revision and
checkpoint. All text is rendered as text, never HTML.

## Scope
- CPU FP32, 2-layer / width-64 / 4-head causal GPT, RoPE, tied 258-byte vocabulary.
- No pretrained weights, external text, network model calls, or automatic learning
  from generated text. This is a nanoGPT-style independent implementation.
- Persistent SQLite jobs, one worker, finite-update checks, replay of previously
  learned page versions on the current branch only. Initial repeat count 2.
- Immutable checkpoints contain model, AdamW, torch RNG, Python sampling RNG,
  configuration, learned-version map, and update count.
- Atomic file save precedes transactional checkpoint publication and job success.
- Restore creates a new child of the selected checkpoint; previous branch retained.
- Seven-day rollback window; current checkpoint is always retained. Logs/pages
  have independent retention. Page deletion is soft deletion, not model unlearning.
- Localhost only, one process, no CORS, mutation header and same-origin validation;
  no account/authentication or public-hosting support. Runtime data lives outside
  the repository by default. No private documents are fixtures.

## Known limits
No conversational quality guarantee. Initial byte output can be invalid UTF-8;
raw tokens are kept, display uses replacement characters. Inference uses at most
the configured training context, with explicit truncation metadata. Chunk training
resets context at chunk boundaries, but never adds false BOS/EOS at those boundaries.
No automatic quality promotion gate until genuine held-out data exists. Loss on
a fed page is fitting, not proof of generalization. Interrupted jobs are failed
without publication; queued jobs survive restarts. Uploaded checkpoints unsupported.
