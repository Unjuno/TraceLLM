"""Platform selection tests do not change the test host's OS or real home."""
import importlib
from pathlib import Path
import threading

import pytest


def runtime():
    import importlib.util
    assert importlib.util.find_spec('trace_llm.runtime'), 'desktop runtime helpers missing'
    return importlib.import_module('trace_llm.runtime')


@pytest.mark.parametrize('system,relative', [
    ('win32', 'AppData/Local/TraceLLM'),
    ('darwin', 'Library/Application Support/TraceLLM'),
    ('linux', '.local/share/trace-llm'),
])
def test_native_new_paths(tmp_path, system, relative):
    m = runtime()
    assert m.resolve_data_dir(platform_name=system, home=tmp_path, environ={}) == tmp_path/relative
    assert list(tmp_path.iterdir()) == []  # resolving never creates or migrates data


@pytest.mark.parametrize('system', ['darwin', 'win32', 'linux'])
def test_existing_legacy_store_keeps_its_path(tmp_path, system):
    legacy = tmp_path/'.local/share/trace-llm'
    legacy.mkdir(parents=True); (legacy/'trace.sqlite3').write_bytes(b'untouched sentinel')
    m = runtime()
    assert m.resolve_data_dir(platform_name=system, home=tmp_path, environ={}) == legacy
    assert (legacy/'trace.sqlite3').read_bytes() == b'untouched sentinel'


def test_explicit_then_environment_then_legacy(tmp_path):
    m = runtime()
    env = {'TRACE_DATA_DIR': str(tmp_path/'environment')}
    assert m.resolve_data_dir(tmp_path/'explicit', environ=env) == tmp_path/'explicit'
    assert m.resolve_data_dir(environ=env) == tmp_path/'environment'


def test_os_environment_directory(tmp_path):
    m = runtime()
    assert m.resolve_data_dir(platform_name='win32', home=tmp_path,
        environ={'LOCALAPPDATA': str(tmp_path/'local')}) == tmp_path/'local/TraceLLM'
    assert m.resolve_data_dir(platform_name='linux', home=tmp_path,
        environ={'XDG_DATA_HOME': str(tmp_path/'xdg')}) == tmp_path/'xdg/trace-llm'
    assert m.resolve_data_dir(platform_name='linux', home=tmp_path,
        environ={'XDG_DATA_HOME': 'relative-is-not-valid'}) == tmp_path/'.local/share/trace-llm'


def test_browser_only_opens_after_this_server_is_ready():
    m = runtime()
    calls = []
    class Server:
        started = False
    server, done = Server(), threading.Event()
    thread = threading.Thread(target=m.open_when_ready,
        args=(server, 'http://127.0.0.1:8000', done, calls.append))
    thread.start()
    assert calls == []
    server.started = True
    thread.join(timeout=2)
    done.set(); thread.join(timeout=2)
    assert calls == ['http://127.0.0.1:8000']


def test_browser_does_not_open_after_server_failure():
    m = runtime()
    calls, done = [], threading.Event()
    class FailedServer:
        started = False
    done.set()
    m.open_when_ready(FailedServer(), 'http://127.0.0.1:8000', done, calls.append)
    assert calls == []


def test_local_server_never_binds_to_all_interfaces(monkeypatch):
    m = runtime()
    seen = {}
    class Server:
        started = False
        def __init__(self, config):
            seen['config'] = config
        def run(self):
            seen['ran'] = True
    monkeypatch.setattr(m.uvicorn, 'Server', Server)
    m.run_local(object(), 8099, False)
    assert seen['ran'] and seen['config'].host == '127.0.0.1'
    assert seen['config'].port == 8099 and seen['config'].workers == 1


def test_setup_helper_has_cpu_only_install_commands():
    path = Path(__file__).parents[1]/'scripts/install.py'
    assert path.is_file(), 'portable installer missing'
    spec = importlib.util.spec_from_file_location('trace_setup_test', path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    commands = mod.install_commands(Path('/work space/.venv/bin/python'), Path('/work space'), 'linux')
    assert '--index-url' in commands[0] and commands[0][-1].endswith('/whl/cpu')
    assert commands[1][-1] == str(Path('/work space'))
    assert mod.platform_problem('darwin', 'x86_64') is not None
    assert mod.platform_problem('darwin', 'arm64') is None
    assert mod.platform_problem('win32', 'AMD64') is None
