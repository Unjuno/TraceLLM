from pathlib import Path
import importlib
import importlib.util

import pytest


def resolve(*args, **kwargs):
    # Explicit RED assertion rather than a misleading module-import collection error.
    assert importlib.util.find_spec('trace_llm.paths'), 'platform path resolver not implemented'
    return importlib.import_module('trace_llm.paths').resolve_data_dir(*args, **kwargs)


@pytest.mark.parametrize('platform,suffix', [
    ('win32', 'AppData/Local/TraceLLM'),
    ('darwin', 'Library/Application Support/TraceLLM'),
    ('linux', '.local/share/trace-llm'),
])
def test_new_platform_paths(tmp_path, platform, suffix):
    assert resolve(home=tmp_path, platform=platform, env={}) == tmp_path / suffix


@pytest.mark.parametrize('platform', ['win32', 'darwin', 'linux'])
def test_existing_legacy_store_wins_without_moving_it(tmp_path, platform):
    legacy = tmp_path / '.local/share/trace-llm'
    legacy.mkdir(parents=True); (legacy / 'trace.sqlite3').write_bytes(b'test marker')
    assert resolve(home=tmp_path, platform=platform, env={}) == legacy
    assert (legacy / 'trace.sqlite3').read_bytes() == b'test marker'


def test_explicit_and_environment_precedence(tmp_path):
    explicit, env_dir = tmp_path/'explicit', tmp_path/'environment'
    assert resolve(explicit, home=tmp_path, env={'TRACE_DATA_DIR':str(env_dir)}) == explicit
    assert resolve(home=tmp_path, env={'TRACE_DATA_DIR':str(env_dir)}) == env_dir
    assert resolve(home=tmp_path, platform='win32', env={'LOCALAPPDATA':str(tmp_path/'Local')}) == tmp_path/'Local/TraceLLM'
    assert resolve(home=tmp_path, platform='linux', env={'XDG_DATA_HOME':str(tmp_path/'xdg')}) == tmp_path/'xdg/trace-llm'
    assert resolve(home=tmp_path, platform='linux', env={'XDG_DATA_HOME':'relative'}) == tmp_path/'.local/share/trace-llm'


def test_path_resolution_does_not_create_or_modify_a_directory(tmp_path):
    before = list(tmp_path.iterdir())
    resolve(home=tmp_path, platform='darwin', env={})
    assert list(tmp_path.iterdir()) == before


def test_python_module_entry_exists():
    assert importlib.util.find_spec('trace_llm.__main__'), 'module launch entry not implemented'
