import os
import unittest
from pathlib import Path
from unittest import mock

from duet.config import CODE_ROOT, Paths, paths, vault_path

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'


class ConfigTests(unittest.TestCase):
    def test_paths_live_under_private(self):
        p = paths(vault=FIXTURE)
        self.assertIsInstance(p, Paths)
        self.assertEqual(p.db, FIXTURE / 'private' / 'graph' / 'duet.lbug')
        self.assertEqual(p.journal, FIXTURE / 'private' / 'graph' / 'journal.jsonl')
        self.assertEqual(p.index_state, FIXTURE / 'private' / 'graph' / 'index-state.json')
        self.assertEqual(p.index_report, FIXTURE / 'private' / 'graph' / 'index-report.json')
        self.assertEqual(p.lock, FIXTURE / 'private' / 'graph' / 'duet.lock')
        self.assertEqual(p.views, FIXTURE / 'private' / 'views')

    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {'KARTEEK_VAULT': str(FIXTURE)}):
            self.assertEqual(vault_path(), FIXTURE)

    def test_missing_setting_exits_with_hint(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('duet.config.CODE_ROOT', CODE_ROOT / 'nonexistent'):
            with self.assertRaises(SystemExit) as ctx:
                vault_path()
        self.assertIn('.env.example', str(ctx.exception))

    def test_rejects_folder_without_agents_md(self):
        with mock.patch.dict(os.environ, {'KARTEEK_VAULT': str(FIXTURE.parent)}):
            with self.assertRaises(SystemExit):
                vault_path()


if __name__ == '__main__':
    unittest.main()
