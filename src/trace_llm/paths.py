"""Desktop data paths. Resolve only; never migrate, create or delete user data."""
import os
from pathlib import Path
import sys
from typing import Mapping


def resolve_data_dir(explicit=None, *, home: Path | None = None, platform: str | None = None,
                     env: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if env is None else env
    home = Path.home() if home is None else Path(home)
    platform = sys.platform if platform is None else platform
    if explicit is not None:
        return Path(explicit).expanduser()
    if environment.get('TRACE_DATA_DIR'):
        return Path(environment['TRACE_DATA_DIR']).expanduser()
    legacy = home / '.local' / 'share' / 'trace-llm'
    # v0.1/0.2 used this directory on every OS. Never silently start a new model
    # instead of opening an existing (even damaged/unsupported) database.
    if (legacy / 'trace.sqlite3').is_file():
        return legacy
    if platform == 'win32':
        local = environment.get('LOCALAPPDATA')
        return (Path(local) if local else home/'AppData'/'Local') / 'TraceLLM'
    if platform == 'darwin':
        return home / 'Library' / 'Application Support' / 'TraceLLM'
    xdg = environment.get('XDG_DATA_HOME')
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else home/'.local'/'share'
    return base / 'trace-llm'
