# Security and privacy boundary

TraceLLM v0.4 is a local, single-user research application, not an internet service.
The CLI binds only to 127.0.0.1. Do not put it behind a public proxy or expose its port.
There is no account authentication, tenant isolation, or at-rest encryption.
Use OS disk encryption and a private data directory. Local users/processes with
filesystem access can read notes, responses, training states and model parameters.
The collapsed output is a UI convenience, not an access-control mechanism.

The HTTP layer validates loopback Host names, rejects cross-origin mutations and
requires a custom mutation header. It does not enable CORS. These are browser
cross-site protections, not authentication against malicious local processes.
The UI renders note/model text as text, never executable HTML. Request, page,
generation and queue sizes are bounded. Runtime data is outside the source tree
by default. No telemetry, pretrained weights, API keys or network inference.

Only application-created checkpoint files are read. IDs are resolved through the
DB rather than accepting arbitrary filesystem paths. Checkpoints are hashed,
loaded with `torch.load(weights_only=True)`, and version checked. Hash verification
is corruption detection, not a defense against someone who can modify both the DB
and checkpoint directory. Checkpoint upload/import is deliberately unsupported.

Deletion in the UI is **soft deletion**. Historical snapshots remain in SQLite,
and previously learned weights may retain information. Seven-day retention applies
only to noncurrent checkpoint files, not pages, responses or audit history. Restore
is branching, not selective machine unlearning. For complete local erasure, stop
the application and remove its entire data directory and any backups; SSD snapshots
and OS backups require separate handling. An export/purge workflow is not implemented.

Before making commits, inspect `git diff --cached` and `git ls-files`; ignore rules
are safeguards, not a guarantee. Never publish real notes, SQLite files, training
checkpoints, generated personal text, credentials or `.env` files. Automated tests
use only small artificial strings and temporary data directories.
