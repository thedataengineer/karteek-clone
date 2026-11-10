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

    def fake_claude(self, payload):
        """Put a stand-in `claude` first on PATH that prints a canned envelope, and give the vault what a check-in reads."""
        tools = Path(tempfile.mkdtemp())
        (tools / 'envelope.json').write_text(json.dumps({'subtype': 'success', 'structured_output': payload}))
        script = tools / 'claude'
        script.write_text(f'#!/bin/sh\ncat > /dev/null\ncat "{tools}/envelope.json"\n')
        script.chmod(0o755)
        self.env['PATH'] = f'{tools}{os.pathsep}{self.env["PATH"]}'
        (self.vault / 'workflows').mkdir(exist_ok=True)
        (self.vault / 'workflows/manifest.json').write_text(json.dumps([
            {'id': 'meeting-prep', 'title': 'Client meeting preparation', 'area': 'advisory', 'execution': 'connected-context',
             'path': 'workflows/playbooks/meeting-prep.md'}]))
        (self.vault / 'workflows/check-in-prompt.md').write_text('Do one check-in.\n')
        (self.vault / '.mcp.json').write_text('{"mcpServers": {}}')

    def test_checkin_writes_a_note_and_rebuild_keeps_the_item_and_the_edit_made_in_the_note(self):
        self.fake_claude({
            'coverage': ['Granola read'],
            'meetings': [{'key': 'granola:7', 'title': 'Review', 'start': '2026-11-09T15:00:00+00:00', 'source': 'granola:7'}],
            'memories': [{'key': 'granola:7:d1', 'source': 'granola:7', 'source_date': '2026-11-09', 'kind': 'decision',
                          'summary': 'Go with option B', 'meeting': 'granola:7'}],
            'items': [{'workflow': 'meeting-prep', 'key': 'granola:7:prep', 'revision': '1', 'title': 'Prep for the review',
                       'why_now': 'Meeting tomorrow', 'draft': 'Questions to ask', 'evidence': 'granola:7', 'rests_on': ['granola:7']}]})
        dry = self.duet('checkin', '--dry-run')
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn('--json-schema', json.loads(dry.stdout)['command'])
        self.assertFalse((self.vault / 'private/duet').exists())
        run = self.duet('checkin')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Saved 1 new item(s)', run.stdout)
        notes = list((self.vault / 'private/duet').glob('*.md'))
        self.assertEqual(len(notes), 1)
        self.assertIn('rests on: [[private/views/meetings/2026-11-09-review|Review]]', notes[0].read_text())
        again = self.duet('checkin')
        self.assertIn('Saved 0 new item(s)', again.stdout)
        notes[0].write_text(notes[0].read_text().replace('status: surfaced', 'status: done'))
        index = self.duet('index')
        self.assertEqual(index.returncode, 0, index.stderr)
        self.assertIn('status: done', (self.vault / 'private/views/items.md').read_text())
        before = [line for line in self.duet('status').stdout.splitlines() if line.startswith('counts: ')]
        self.assertIn('DuetItem=1', before[0])
        self.assertIn('Meeting=1', before[0])
        rebuilt = self.duet('rebuild')
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        self.assertEqual([line for line in self.duet('status').stdout.splitlines() if line.startswith('counts: ')], before)
        self.assertIn('status: done', (self.vault / 'private/views/items.md').read_text())

    def test_checkin_stops_with_exit_1_and_no_note_when_claude_returns_an_invalid_result(self):
        self.fake_claude({'coverage': [], 'memories': [], 'items': [{'workflow': 'not-a-playbook', 'key': 'k', 'revision': '1',
                                                                      'title': 't', 'why_now': 'w', 'draft': 'd', 'evidence': 'e'}]})
        run = self.duet('checkin')
        self.assertEqual(run.returncode, 1)
        self.assertIn('Check-in stopped: Unknown workflow ID.', run.stderr)
        self.assertFalse((self.vault / 'private/duet').exists())

    def test_schedule_writes_the_file_without_installing_it(self):
        run = self.duet('schedule', '--hour', '7', '--minute', '0', '--notify')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('not installed', run.stdout)
        self.assertTrue((self.vault / 'private/ai.karteek-clone.check-in.plist').exists())


if __name__ == '__main__':
    unittest.main()
