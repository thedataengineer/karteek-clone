import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from duet import daily
from duet.changeset import Changeset, Node
from duet.config import paths
from duet.ecl import index
from duet.graph import Graph

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'
NOTE = 'private/duet/2026-11-10.md'


def item_node(key, **over):
    props = dict(label=f'Title {key}', scope='private', owner='assistant', source_locator='granola:1', defined_in=NOTE,
                 key=key, revision='1', status='surfaced', snooze_until='', why_now='soon', draft='Draft text')
    props.update(over)
    return Node('DuetItem', f'item:{key}', props)


def section(key, status='surfaced', draft='Draft text', title=None):
    return (f'## {title or "Title " + key}\nkey: {key}\nwhy now: soon\nstatus: {status}\n'
            f'playbook: [[workflows/playbooks/meeting-prep]]\nevidence: granola:1\n\n{draft}\n')


class NoteFormatTests(unittest.TestCase):
    def test_draft_headings_are_demoted_below_section_level_and_stay_put(self):
        draft = '# Plan\n\ntext\n\n## Step\n\n```\n# not a heading\n```\n'
        once = daily.normalize_draft(draft)
        self.assertEqual(once, '### Plan\n\ntext\n\n#### Step\n\n```\n# not a heading\n```')
        self.assertEqual(daily.normalize_draft(once), once)
        self.assertEqual(daily.normalize_draft('### Already low\n\nx'), '### Already low\n\nx')

    def test_parse_reads_status_snooze_and_draft_and_ignores_sections_without_a_key(self):
        text = ('# Duet, 2026-11-10\n\n## Source coverage, run 07:00\n\n- Granola read\n\n'
                + section('a', 'done') + '\n' + section('b', 'snoozed 2026-11-20', 'Line one\n\n### Sub\n\nLine two')
                + '\n## My own heading\n\nfree text\n')
        found = daily.parse_note(text)
        self.assertEqual(sorted(found), ['a', 'b'])
        self.assertEqual((found['a'].status, found['a'].snooze_until, found['a'].draft), ('done', '', 'Draft text'))
        self.assertEqual((found['b'].status, found['b'].snooze_until), ('snoozed', '2026-11-20'))
        self.assertEqual(found['b'].draft, 'Line one\n\n### Sub\n\nLine two')

    def test_parse_flags_a_status_it_does_not_understand(self):
        found = daily.parse_note(section('a', 'maybe later') + section('b', 'snoozed') + section('c', 'done 2026-11-20'))
        self.assertTrue(all(found[key].problem for key in 'abc'))

    def test_a_fenced_heading_does_not_end_a_section_and_the_last_section_for_a_key_wins(self):
        text = section('a', 'surfaced', 'see\n\n```\n## inside a fence\n```\n\nend') + section('a', 'done', 'second')
        found = daily.parse_note(section('z') + text)
        self.assertEqual((found['a'].status, found['a'].draft), ('done', 'second'))
        only = daily.parse_note(section('a', 'surfaced', 'see\n\n```\n## inside a fence\n```\n\nend'))
        self.assertTrue(only['a'].draft.endswith('end'))

    def test_append_run_keeps_what_is_already_in_the_note(self):
        now = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)
        first = daily.append_run('', '2026-11-10', now, ['Granola read'], [section('a')])
        self.assertTrue(first.startswith('# Duet, 2026-11-10\n'))
        self.assertIn('## Source coverage, run 07:00', first)
        edited = first.replace('status: surfaced', 'status: done') + '\nmy own line\n'
        second = daily.append_run(edited, '2026-11-10', now.replace(hour=12), ['Plaud failed: not signed in'], [])
        self.assertIn('status: done', second)
        self.assertIn('my own line', second)
        self.assertIn('## Source coverage, run 12:00', second)
        self.assertIn('- Plaud failed: not signed in', second)
        self.assertIn('No new items in this run.', second)
        self.assertEqual(sorted(daily.parse_note(second)), ['a'])


class ReadBackTests(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp()) / 'vault'
        shutil.copytree(FIXTURE, self.vault)
        self.p = paths(vault=self.vault)
        self.graph = Graph.open(self.p)
        self.addCleanup(self.graph.close)
        self.graph.ensure_schema()
        self.graph.apply(Changeset(nodes=[item_node('a'), item_node('b'), item_node('c')]), 'runner')
        self.note = self.vault / NOTE
        self.note.parent.mkdir(parents=True)

    def item(self, key):
        return self.graph.node(f'item:{key}')[1]

    def test_status_and_draft_edits_reach_the_graph_and_a_deleted_section_dismisses(self):
        self.note.write_text(section('a', 'done') + section('b', 'snoozed 2026-11-20', 'Rewritten draft'))
        out = daily.read_back(self.graph, self.p)
        self.assertEqual(sorted(out['updated']), ['item:a', 'item:b', 'item:c'])
        self.assertEqual(self.item('a')['status'], 'done')
        self.assertEqual((self.item('b')['status'], self.item('b')['snooze_until'], self.item('b')['draft']),
                         ('snoozed', '2026-11-20', 'Rewritten draft'))
        self.assertEqual(self.item('c')['status'], 'dismissed')
        self.assertEqual(self.item('a')['scope'], 'private')
        self.assertEqual(daily.read_back(self.graph, self.p)['updated'], [])  # nothing changed since

    def test_a_missing_note_changes_nothing(self):
        out = daily.read_back(self.graph, self.p)
        self.assertEqual(out, {'updated': [], 'problems': []})
        self.assertEqual(self.item('a')['status'], 'surfaced')

    def test_an_unreadable_status_is_reported_and_leaves_the_item_alone(self):
        self.note.write_text(section('a', 'perhaps') + section('b') + section('c'))
        out = daily.read_back(self.graph, self.p)
        self.assertEqual(out['updated'], [])
        self.assertEqual(out['problems'][0]['key'], 'a')
        self.assertEqual(self.item('a')['status'], 'surfaced')

    def test_index_runs_the_read_back_and_reports_it(self):
        self.note.write_text(section('a', 'dismissed') + section('b') + section('c'))
        report = index(self.vault, self.graph)
        self.assertEqual(report['notes']['updated'], ['item:a'])
        self.assertEqual(self.item('a')['status'], 'dismissed')
        self.assertNotIn(NOTE, report['parsed'])  # daily notes are not pages


if __name__ == '__main__':
    unittest.main()
