"""Compatibility shim: the vault locator now lives in duet.config."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duet.config import CODE_ROOT, vault_path  # noqa: E402,F401
