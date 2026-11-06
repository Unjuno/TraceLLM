# TraceLLM v0.4.0 — local CPU-first journal

First release with an explicit editor/training state machine and learning-health UI.

- Local-only note editor with immutable revisions; training requires deliberate feeding (swipe or menu). No external training data, pretrained models, or automatic reuse of generated text.
- 115,136-parameter scratch GPT on CPU (PyTorch FP32); replay, token accounting, 7-day checkpoint rollback and branch-aware learning history.
- Learning-health screen differentiates job success, first-seen loss, past-page retention probes and unverified generalization.
- Recover unsaved drafts from revision conflicts by saving to a new page, instead of overwriting another edit.
- Publish-time guards reject training on a deleted source; checkpoint files are cleaned up after failed publication.
- A crashed background worker is now visible as *stopped* rather than appearing to process queued pages.
- Local data stays outside the repository. Do not deploy the unauthenticated app to a public server.

## Installation

Requires Python 3.11–3.13. Install dependencies once; the running model uses no network service.

```shell
python -m pip install trace_llm-0.4.0-py3-none-any.whl
trace-llm
```

Open http://127.0.0.1:8000 on the same machine. Windows/macOS/Linux are covered by CI tests, not native installers. See README for local data paths and complete setup instructions.

## Limitations

- A model trained from scratch on a few pages will produce poor text; fluent conversation is **not** demonstrated or promised.
- Local disk encryption, export, complete deletion/unlearning and independently held-out language evaluation are not implemented.
- A browser draft can still be lost on a crash before it is saved. Back up the full data directory with TraceLLM stopped.
- Inference may wait for training; strict CPU quotas and GPU acceleration are not implemented.
- The repository has no license grant yet. A public source repository does not imply permission to reuse or redistribute.
