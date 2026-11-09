"""Locate the Karteek knowledge vault that the code operates on."""
import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]


def vault_path():
    """Return the vault directory from KARTEEK_VAULT or the repo's .env file."""
    value = os.environ.get('KARTEEK_VAULT')
    if not value:
        env = CODE_ROOT / '.env'
        if env.is_file():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith('KARTEEK_VAULT='):
                    value = line.split('=', 1)[1].strip().strip('"').strip("'")
                    break
    if not value:
        raise SystemExit('Set KARTEEK_VAULT (env or .env) to the vault directory. See .env.example.')
    path = Path(value).expanduser()
    if not (path / 'AGENTS.md').is_file():
        raise SystemExit(f'{path} does not look like the Karteek vault (no AGENTS.md).')
    return path
