import json
import plistlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from duet import checkin
from duet.changeset import GateError
from duet.config import paths
from duet.graph import Graph

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'
MANIFEST = [
    {'id': 'meeting-capture', 'title': 'Meeting capture', 'area': 'advisory', 'execution': 'connected-context',
     'path': 'workflows/playbooks/meeting-capture.md'},
    {'id': 'meeting-prep', 'title': 'Client meeting preparation', 'area': 'advisory', 'execution': 'connected-context',
     'path': 'workflows/playbooks/meeting-prep.md'},
]
WORKFLOWS = {entry['id'] for entry in MANIFEST}
NOW = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)


def item(revision='1', **over):
    out = dict(workflow='meeting-capture', key='meeting:123:followup', revision=revision, title='Draft follow-up',
               why_now='Meeting finished', draft='Draft text', evidence='granola:123')
    out.update(over)
    return out


def memory(kind='decision', key='granola:123:decision-1', **over):
    out = dict(key=key, source='granola:123', source_date='2026-11-09', kind=kind, summary='Go with option B')
    out.update(over)
    return out


def meeting(**over):
    out = dict(key='granola:123', title='Architecture review', start='2026-11-09T15:00:00-05:00', source='granola:123')
    out.update(over)
    return out


def envelope(items=(), memories=(), meetings=None, coverage=('Granola read',), subtype='success'):
    payload = dict(items=list(items), coverage=list(coverage), memories=list(memories))
    if meetings is not None:
        payload['meetings'] = list(meetings)
    return json.dumps(dict(subtype=subtype, structured_output=payload))


class ParseResultTests(unittest.TestCase):
    def parse(self, **kw):
        return checkin.parse_result(envelope(**kw), WORKFLOWS)

    def test_accepts_valid_and_rejects_error(self):
        result = self.parse(items=[item()])
        self.assertEqual(result['items'], [item()])
        self.assertEqual(result['meetings'], [])  # optional in the result, always present after parsing
        with self.assertRaises(ValueError):
            self.parse(items=[item()], subtype='error_max_budget_usd')

    def test_rejects_missing_evidence_duplicate_keys_and_unknown_workflow(self):
        for items in ([item(evidence='')], [item(), item()], [item(workflow='nope')], [item(extra='x')]):
            with self.assertRaises(ValueError):
                self.parse(items=items)

    def test_rejects_unsourced_memory_and_unknown_kind(self):
        for bad in (memory(source=''), memory(kind='rumour'), memory(surprise='x')):
            with self.assertRaises(ValueError):
                self.parse(memories=[bad])

    def test_account_context_needs_an_account(self):
        with self.assertRaises(ValueError):
            self.parse(memories=[memory(kind='account-context')])
        self.parse(memories=[memory(kind='account-context', account='Acme')])

    def test_references_must_resolve_inside_the_result(self):
        with self.assertRaises(ValueError):
            self.parse(memories=[memory(meeting='granola:999')])
        with self.assertRaises(ValueError):
            self.parse(items=[item(rests_on=['nope'])])
        with self.assertRaises(ValueError):  # one key cannot name both a meeting and a memory
            self.parse(memories=[memory(key='granola:123')], meetings=[meeting()])
        ok = self.parse(items=[item(rests_on=['granola:123', 'granola:123:decision-1'])],
                        memories=[memory(meeting='granola:123')], meetings=[meeting()])
        self.assertEqual(len(ok['meetings']), 1)


class SelectNewTests(unittest.TestCase):
    def select(self, items, prior, now=NOW):
        return checkin.select_new(items, lambda key: prior.get(key), now)

    def test_suppresses_duplicate_but_surfaces_material_revision(self):
        prior = {item()['key']: dict(status='surfaced', revision='1')}
        self.assertEqual(self.select([item()], prior), [])
        self.assertEqual(self.select([item('2')], prior), [item('2')])

    def test_dismissal_and_done_survive_a_new_revision(self):
        for status in ('dismissed', 'done'):
            self.assertEqual(self.select([item('2')], {item()['key']: dict(status=status, revision='1')}), [])

    def test_snooze_expiry_with_a_date_or_a_timestamp(self):
        for until in ('2026-11-12', '2026-11-12T09:00:00-05:00'):
            prior = {item()['key']: dict(status='snoozed', revision='1', snooze_until=until)}
            self.assertEqual(self.select([item()], prior, datetime(2026, 11, 10, tzinfo=timezone.utc)), [])
            self.assertEqual(self.select([item()], prior, datetime(2026, 11, 14, tzinfo=timezone.utc)), [item()])


class FakeClaude:
    """Stands in for the Claude process: records the call and returns a canned envelope."""

    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode, self.calls = stdout, returncode, []

    def __call__(self, command, **kw):
        self.calls.append((command, kw))
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr='')


class RunTests(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp()) / 'vault'
        shutil.copytree(FIXTURE, self.vault)
        (self.vault / 'workflows').mkdir()
        (self.vault / 'workflows/manifest.json').write_text(json.dumps(MANIFEST))
        (self.vault / 'workflows/check-in-prompt.md').write_text('Do one check-in.\n')
        (self.vault / '.mcp.json').write_text('{"mcpServers": {}}')
        self.p = paths(vault=self.vault)
        self.said = []
        self.notified = []

    def run_once(self, stdout, now=NOW, notify=False, returncode=0):
        fake = FakeClaude(stdout, returncode)
        code = checkin.run(self.p, notify=notify, now=now, runner=fake, claude='/bin/claude', out=self.said.append,
                           notifier=lambda: self.notified.append(True))
        return code, fake

    def node(self, node_id):
        with Graph.open(self.p, read_only=True) as graph:
            return graph.node(node_id), graph.neighbors(node_id, None, 'out')

    def full(self):
        return envelope(
            items=[item(rests_on=['granola:123', 'granola:123:commit-1'], draft='# Follow-up\n\nHello')],
            memories=[memory(meeting='granola:123', account='Acme'),
                      memory(kind='commitment', key='granola:123:commit-1', summary='Send the estimate'),
                      memory(kind='account-context', key='granola:123:ctx', summary='Acme is comparing two vendors', account='Acme'),
                      memory(kind='open-question', key='granola:123:q', summary='Who signs off?')],
            meetings=[meeting(account='Acme')], coverage=['Granola read', 'Plaud failed: not signed in'])

    def test_a_check_in_lands_in_the_graph_and_the_daily_note(self):
        code, fake = self.run_once(self.full(), notify=True)
        self.assertEqual(code, 0)
        (table, props), edges = self.node('item:meeting:123:followup')
        self.assertEqual((table, props['status'], props['revision'], props['scope'], props['owner']),
                         ('DuetItem', 'surfaced', '1', 'private', 'assistant'))
        self.assertEqual(props['draft'], '### Follow-up\n\nHello')
        self.assertEqual(props['defined_in'], 'private/duet/2026-11-10.md')
        self.assertEqual(sorted((rel, other) for rel, _, other, _ in edges),
                         [('RESTS_ON', 'commitment:granola:123:commit-1'), ('RESTS_ON', 'meeting:granola:123'),
                          ('USES', 'playbook:meeting-capture')])
        (table, props), edges = self.node('decision:granola:123:decision-1')
        self.assertEqual((table, props['label'], props['scope']), ('Decision', 'Go with option B', 'private'))
        self.assertEqual(sorted((rel, other) for rel, _, other, _ in edges),
                         [('CONCERNS', 'account:acme'), ('DECIDED_IN', 'meeting:granola:123'), ('SUPPORTED_BY', 'source:ext:granola:123')])
        self.assertEqual(self.node('commitment:granola:123:commit-1')[0][1]['owner_known'], False)
        self.assertEqual(self.node('claim:checkin:granola:123:ctx')[0][1]['status'], 'hypothesis')
        self.assertEqual(self.node('claim:checkin:granola:123:q')[0][1]['status'], 'unknown')
        self.assertEqual(self.node('source:ext:granola:123')[0][1]['kind'], 'meeting')
        self.assertEqual(self.node('meeting:granola:123')[0][1]['provider_ids'], ['granola:123'])

        note = (self.vault / 'private/duet/2026-11-10.md').read_text()
        self.assertIn('## Draft follow-up\nkey: meeting:123:followup\nwhy now: Meeting finished\nstatus: surfaced\n'
                      'playbook: [[workflows/playbooks/meeting-capture]]\n', note)
        self.assertIn('- Plaud failed: not signed in', note)
        self.assertIn('### Follow-up\n\nHello', note)
        self.assertEqual(self.notified, [True])
        self.assertTrue((self.vault / 'private/views/items.md').exists())
        self.assertTrue((self.vault / 'private/views/accounts/acme.md').exists())
        self.assertIn('Architecture review', (self.vault / 'private/views/meetings/2026-11-09-architecture-review.md').read_text())

        command, kw = fake.calls[0]
        self.assertEqual(kw['cwd'], self.vault)
        self.assertIn('mcp__duet__claims', command[command.index('--allowedTools') + 1])
        self.assertNotIn('propose_claim', ' '.join(command))  # the unattended run reads the graph, the runner writes it
        self.assertIn('Do one check-in.', kw['input'])

    def test_second_run_suppresses_the_unchanged_item_and_tells_claude_what_was_surfaced(self):
        self.run_once(envelope(items=[item()]))
        code, fake = self.run_once(envelope(items=[item()]), now=NOW.replace(hour=12), notify=True)
        self.assertEqual(code, 0)
        self.assertIn('meeting:123:followup', fake.calls[0][1]['input'])
        self.assertEqual(self.notified, [])
        note = (self.vault / 'private/duet/2026-11-10.md').read_text()
        self.assertEqual(note.count('key: meeting:123:followup'), 1)
        self.assertIn('## Source coverage, run 12:00', note)
        self.assertIn('Saved 0 new item(s)', self.said[-1])

    def test_a_status_edit_in_the_note_is_read_back_before_the_next_run_decides(self):
        self.run_once(envelope(items=[item()]))
        note = self.vault / 'private/duet/2026-11-10.md'
        note.write_text(note.read_text().replace('status: surfaced', 'status: dismissed'))
        self.run_once(envelope(items=[item('2')]), now=datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(self.node('item:meeting:123:followup')[0][1]['status'], 'dismissed')
        self.assertNotIn('key: meeting:123:followup', (self.vault / 'private/duet/2026-11-11.md').read_text())

    def test_a_new_revision_resurfaces_in_the_new_days_note(self):
        self.run_once(envelope(items=[item()]))
        self.run_once(envelope(items=[item('2', draft='Newer draft')]), now=datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc))
        props = self.node('item:meeting:123:followup')[0][1]
        self.assertEqual((props['revision'], props['defined_in'], props['draft']), ('2', 'private/duet/2026-11-11.md', 'Newer draft'))
        self.assertTrue(props['history'])

    def test_failed_or_invalid_runs_leave_no_note_and_no_nodes(self):
        with self.assertRaises(RuntimeError):
            self.run_once(envelope(items=[item()]), returncode=1)
        with self.assertRaises(ValueError):
            self.run_once(envelope(items=[item(workflow='nope')]))
        self.assertFalse((self.vault / 'private/duet').exists())
        self.assertIsNone(self.node('item:meeting:123:followup')[0])

    def test_a_sign_in_failure_is_named_and_other_provider_text_is_never_printed(self):
        expired = json.dumps(dict(subtype='success', is_error=True, result='Failed to authenticate: OAuth session expired'))
        with self.assertRaises(RuntimeError) as ctx:
            self.run_once(expired, returncode=1)
        self.assertIn('claude auth login', str(ctx.exception))
        private = json.dumps(dict(subtype='error_during_execution', is_error=True, result='Acme said the budget is 2M'))
        with self.assertRaises(RuntimeError) as ctx:
            self.run_once(private, returncode=1)
        self.assertNotIn('Acme', str(ctx.exception))
        self.assertFalse((self.vault / 'private/duet').exists())

    def test_a_gate_rejection_writes_no_note_and_is_journaled(self):
        original = checkin.result_changeset

        def broken(*args, **kw):
            cs, targets = original(*args, **kw)
            cs.edges = [e for e in cs.edges if e.rel != 'SUPPORTED_BY']
            return cs, targets
        checkin.result_changeset = broken
        self.addCleanup(setattr, checkin, 'result_changeset', original)
        with self.assertRaises(GateError):
            self.run_once(envelope(memories=[memory()]))
        self.assertFalse((self.vault / 'private/duet').exists())
        last = json.loads(self.p.journal.read_text().splitlines()[-1])
        self.assertEqual((last['actor'], last['result'], last['gate']), ('runner', 'rejected', 'evidence'))

    def test_a_later_run_keeps_what_was_learned_about_a_commitment(self):
        self.run_once(envelope(memories=[memory(kind='commitment', key='c1', summary='Send the estimate')]))
        with Graph.open(self.p) as graph:
            graph.execute("MATCH (c:Commitment {id: 'commitment:c1'}) SET c.status = 'done', c.due = '2026-11-20'")
        self.run_once(envelope(memories=[memory(kind='commitment', key='c1', summary='Send the estimate to Acme')]),
                      now=NOW.replace(hour=12))
        props = self.node('commitment:c1')[0][1]
        self.assertEqual((props['label'], props['status'], props['due']), ('Send the estimate to Acme', 'done', '2026-11-20'))


class ScheduleTests(unittest.TestCase):
    def test_schedule_is_weekdays_runs_the_package_and_only_notifies_when_asked(self):
        with tempfile.TemporaryDirectory() as folder:
            p = paths(vault=FIXTURE)
            path = Path(folder) / 'check.plist'
            checkin.write_schedule(p, path, 7, 30, False)
            data = plistlib.loads(path.read_bytes())
            self.assertEqual([x['Weekday'] for x in data['StartCalendarInterval']], [1, 2, 3, 4, 5])
            self.assertEqual({(x['Hour'], x['Minute']) for x in data['StartCalendarInterval']}, {(7, 30)})
            self.assertEqual(data['ProgramArguments'][1:], ['-m', 'duet', 'checkin'])
            self.assertNotIn('RunAtLoad', data)
            self.assertEqual(data['EnvironmentVariables']['KARTEEK_VAULT'], str(FIXTURE))
            checkin.write_schedule(p, path, 7, 30, True)
            self.assertEqual(plistlib.loads(path.read_bytes())['ProgramArguments'][-1], '--notify')

    def test_install_copies_the_file_and_loads_it_and_remove_undoes_both(self):
        calls = []

        def launchctl(args, **kw):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with tempfile.TemporaryDirectory() as folder:
            p = paths(vault=FIXTURE)
            source = Path(folder) / 'check.plist'
            agents = Path(folder) / 'LaunchAgents'
            checkin.write_schedule(p, source, 7, 0, True)
            target = checkin.install_schedule(source, agents_dir=agents, runner=launchctl, uid=501)
            self.assertEqual(target, agents / 'ai.karteek-clone.check-in.plist')
            self.assertTrue(target.exists())
            self.assertEqual([c[1] for c in calls], ['bootout', 'bootstrap', 'print'])
            self.assertEqual(calls[1], ['/bin/launchctl', 'bootstrap', 'gui/501', str(target)])
            checkin.remove_schedule(agents_dir=agents, runner=launchctl, uid=501)
            self.assertFalse(target.exists())
            self.assertEqual(calls[-1][:2], ['/bin/launchctl', 'bootout'])


if __name__ == '__main__':
    unittest.main()
