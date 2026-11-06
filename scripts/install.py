#!/usr/bin/env python3
"""Explicit, local-venv setup. Installation downloads packages; the app does not.
Run with Python 3.11-3.13. No administrator privileges, shell interpolation or data migration.
"""
import argparse
from pathlib import Path
import platform
import subprocess
import sys


def platform_problem(system, machine):
    architecture = machine.lower()
    if system == 'darwin' and architecture not in ('arm64', 'aarch64'):
        return ('Intel macOS is not supported by official PyTorch 2.10 wheels. '
                'Do not downgrade PyTorch; use a supported OS/architecture. '
                'On Apple Silicon use a native arm64 Python, not Rosetta.')
    if system == 'win32' and architecture not in ('amd64', 'x86_64'):
        return 'This Windows installer is tested for x64 Python only.'
    if system.startswith('linux') and architecture not in ('x86_64', 'amd64', 'aarch64', 'arm64'):
        return 'A 64-bit x86 or ARM Linux Python is required; ARM Linux is not in the primary CI matrix.'
    if system not in ('win32', 'darwin') and not system.startswith('linux'):
        return 'Supported desktop systems: Windows, macOS and Linux.'
    return None


def install_commands(python: Path, root: Path, system: str):
    torch = [str(python), '-m', 'pip', 'install', 'torch>=2.10,<2.11']
    if system != 'darwin':
        torch += ['--index-url', 'https://download.pytorch.org/whl/cpu']
    return [torch, [str(python), '-m', 'pip', 'install', '-e', str(root)]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check prerequisites without installing')
    args = parser.parse_args()
    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        parser.error('Use Python 3.11, 3.12 or 3.13.')
    error = platform_problem(sys.platform, platform.machine())
    if error:
        parser.error(error)
    print(f'Platform: {sys.platform}/{platform.machine()}, Python {platform.python_version()}')
    if args.check:
        return
    root = Path(__file__).resolve().parents[1]
    env = root / '.venv'
    python = env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(env)], check=True)
    for command in install_commands(python, root, sys.platform):
        subprocess.run(command, check=True)
    print('Setup complete. Use start.cmd (Windows), start.command (macOS), or ./start.sh (Linux).')
    print('Private data are stored outside the source tree. This step did not train a model.')


if __name__ == '__main__':
    main()
