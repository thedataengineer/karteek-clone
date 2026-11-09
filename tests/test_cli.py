import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'


class CliTests(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp()) / 'vault'
        shutil.copytree(FIXTURE, self.vault)
        self.env = dict(os.environ, KARTEEK_VAULT=str(self.vault))

    def duet(self, *args):
        return subprocess.run([sys.executable, '-m', 'duet', *args], env=self.env, capture_output=True, text=True)

    def test_index_project_status(self):
        run = self.duet('index')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('parsed=6 unchanged=0 rejected=0', run.stdout)
        self.assertTrue((self.vault / 'private/views/me.md').exists())
        status = self.duet('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        counts = [line for line in status.stdout.splitlines() if line.startswith('counts: ')][0]
        self.assertIn('Claim=2', counts)
        self.assertIn('Document=6', counts)
        self.assertEqual(self.duet('project').returncode, 0)

    def test_index_exit_code_on_rejection(self):
        (self.vault / 'identity' / 'bad.md').write_text('# Bad\n\n## U\n\n- **Claim:** x\n- **Status:** confirmed\n- **Evidence:** [g](../sources/none.md)\n')
        run = self.duet('index')
        self.assertEqual(run.returncode, 1)
        self.assertIn('rejected identity/bad.md', run.stdout)

    def test_rebuild_replays_claude_journal(self):
        self.assertEqual(self.duet('index').returncode, 0)
        journal = self.vault / 'private/graph/journal.jsonl'
        entry = {'ts': '2026-11-09T09:00:00+00:00', 'actor': 'mcp:claude', 'result': 'applied', 'gate': None, 'message': '',
                 'changeset': {'nodes': [{'table': 'Claim', 'id': 'claim:meeting:likes-demos',
                                          'props': {'label': 'Likes live demos', 'status': 'hypothesis', 'scope': 'private', 'owner': 'assistant'}}],
                               'edges': [{'rel': 'SUPPORTED_BY', 'src': 'claim:meeting:likes-demos', 'dst': 'source:2026-11-08-role', 'locator': 'replay'}],
                               'retire': []}}
        with journal.open('a') as stream:
            stream.write(json.dumps(entry) + '\n')
        lines_before = len(journal.read_text().splitlines())
        run = self.duet('rebuild')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('replayed=1', run.stdout)
        status = self.duet('status').stdout
        self.assertIn('Claim=3', status)
        self.assertGreater(len(journal.read_text().splitlines()), lines_before)
        # a second rebuild replays the same single entry again, not the record of the first replay
        again = self.duet('rebuild')
        self.assertIn('replayed=1', again.stdout)
        self.assertIn('Claim=3', self.duet('status').stdout)
        results = [json.loads(line)['result'] for line in journal.read_text().splitlines()]
        self.assertEqual(results.count('replayed'), 2)
        self.assertIn('claim:meeting:likes-demos', (self.vault / 'private/views/me.md').read_text())

    def test_rebuild_equals_fresh_index_and_keeps_journal(self):
        self.assertEqual(self.duet('index').returncode, 0)
        fresh = [line for line in self.duet('status').stdout.splitlines() if line.startswith('counts: ')]
        journal = self.vault / 'private/graph/journal.jsonl'
        first_line = journal.read_text().splitlines()[0]
        run = self.duet('rebuild')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('rebuilt: parsed=6 rejected=0 replayed=0 replay_failed=0', run.stdout)
        self.assertEqual([line for line in self.duet('status').stdout.splitlines() if line.startswith('counts: ')], fresh)
        self.assertEqual(journal.read_text().splitlines()[0], first_line)


if __name__ == '__main__':
    unittest.main()
