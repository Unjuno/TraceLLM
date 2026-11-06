# Learning health + desktop local runtime

## Intent and constraints
Keep the notebook/swipe/masked-output UI and existing training/checkpoint formats. Add a
read-only Learning State view that distinguishes execution, observed retention, and generalization.
No invented score; no model training, generation, checkpoint deserialization or private-data export
when opening this screen. Preserve seven-day rollback and local-only storage.

## Approaches considered
1. Keep FastAPI and native browser UI (selected): no second runtime/build service, existing
   CSP and CPU model preserved. Distribution requires Python rather than a signed desktop binary.
2. React/Vite SPA: component ecosystem but adds a frontend toolchain with no necessary capability here.
3. Electron/Tauri: native shell/installer, but runtime/bundling/signing complexity and extra resources.

## Screen
Sidebar: Pages / Learning state / History. The new view has an explicit current-checkpoint
and seven-day scope; execution covers all operations, accepted-model observations cover only
current ancestry (parent_id, never previous_id), capped at latest 200 records with truncation notice.
Cards: processing state, last retention measurement, independent validation unavailable.
Charts: dots for first-page pre-training NLL, never connect different pages as a growth curve.
Training/replay exposures and timing are numeric observations, not intelligence or CPU utilization.
Tables disclose sample count, units, timestamp, stop reason and context. Empty/error/legacy/branch
states are first-class. Restore actions remain in History; output stays masked in Pages.

## Runtime
Python 3.11-3.13; CPU torch 2.10; Windows x64, macOS Apple Silicon, Linux x64 are primary CI targets.
Intel macOS has no official torch 2.10 wheel: do not silently downgrade PyTorch. Linux on Intel
hardware is a separate option, not a claim of macOS native support. No mobile guarantee.
OS-native data directories for new users; explicit argument > env > existing legacy store > native
path. Never move data automatically. Optional browser launch only after the server actually starts.
App still binds 127.0.0.1, not LAN; no external JS/CDN/font/telemetry.

## Acceptance
Existing 70 tests plus regression tests, API read-only checks under training lock, old-store
preservation, empty/failure/partial/restore/expired snapshots, finite JSON. Browser test exercises
health navigation and an actual update, with console/network checks and desktop screenshot.
Cross-platform CI must distinguish scheduled/passing/failed; unsupported architectures documented.
