"""Locate the knowledge vault and derive every path the code writes to."""
import os
from dataclasses import dataclass
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]


def vault_path() -> Path:
    """Return the vault directory from KARTEEK_VAULT or the code repo's .env file."""
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


@dataclass(frozen=True)
class Paths:
    vault: Path

    @property
    def graph_dir(self) -> Path:
        return self.vault / 'private' / 'graph'

    @property
    def db(self) -> Path:
        return self.graph_dir / 'duet.lbug'

    @property
    def journal(self) -> Path:
        return self.graph_dir / 'journal.jsonl'

    @property
    def index_state(self) -> Path:
        return self.graph_dir / 'index-state.json'

    @property
    def index_report(self) -> Path:
        return self.graph_dir / 'index-report.json'

    @property
    def lock(self) -> Path:
        return self.graph_dir / 'duet.lock'

    @property
    def views(self) -> Path:
        return self.vault / 'private' / 'views'


def paths(vault=None) -> Paths:
    return Paths(Path(vault).expanduser() if vault else vault_path())
