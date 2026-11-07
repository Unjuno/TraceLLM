"""Release pipeline guardrails, including exactly one CI per PR update."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_push_and_pull_request_do_not_duplicate_desktop_ci():
    workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert '\non:\n  push:\n    branches: [main]\n  pull_request:\n' in workflow
    assert 'fail-fast: false' in workflow


def test_release_gated_on_successful_main_push_and_notes_present():
    workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert 'gh release create' in workflow
    assert (ROOT / 'docs/release-notes-v0.4.0.md').is_file()
