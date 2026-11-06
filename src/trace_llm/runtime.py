"""Portable local startup. Resolving a path never migrates or creates private data."""
from collections.abc import Mapping
import logging
import os
from pathlib import Path
import sys
import threading
import webbrowser

import uvicorn


def resolve_data_dir(explicit=None, *, environ: Mapping[str, str] | None = None,
                     platform_name: str | None = None, home: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    system = sys.platform if platform_name is None else platform_name
    if explicit is not None:
        return Path(explicit).expanduser()
    if env.get('TRACE_DATA_DIR'):
        return Path(env['TRACE_DATA_DIR']).expanduser()
    legacy = home / '.local' / 'share' / 'trace-llm'
    if (legacy / 'trace.sqlite3').is_file():
        # v0.1/v0.2 users must never appear to lose their notes after an upgrade.
        return legacy
    if system == 'win32':
        base = Path(env['LOCALAPPDATA']) if env.get('LOCALAPPDATA') else home/'AppData'/'Local'
        return base / 'TraceLLM'
    if system == 'darwin':
        return home / 'Library' / 'Application Support' / 'TraceLLM'
    xdg = Path(env['XDG_DATA_HOME']) if env.get('XDG_DATA_HOME') else None
    return xdg/'trace-llm' if xdg is not None and xdg.is_absolute() else legacy


def open_when_ready(server, url, done: threading.Event, opener=webbrowser.open):
    """Never open an unrelated app occupying the port when this server fails to start."""
    while not done.wait(.05):
        if server.started:
            try:
                opener(url)
            except Exception:
                logging.getLogger('trace_llm').warning('Open this address in your browser: %s', url)
            return


def run_local(app, port: int, open_browser: bool = False):
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, workers=1,
                                         access_log=False))
    done = threading.Event()
    thread = None
    if open_browser:
        thread = threading.Thread(target=open_when_ready,
            args=(server, f'http://127.0.0.1:{port}', done), daemon=True, name='trace-browser-opener')
        thread.start()
    try:
        server.run()
    finally:
        done.set()
        if thread:
            thread.join(timeout=1)
