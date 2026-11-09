import shutil
import tempfile
import unittest
from pathlib import Path

from duet.changeset import Changeset, Edge, Node
from duet.config import paths
from duet.ecl import index
from duet.graph import Graph
from duet.project import project

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp()) / 'vault'
        shutil.copytree(FIXTURE, self.vault)
        self.p = paths(vault=self.vault)
        with Graph.open(self.p) as g:
            g.ensure_schema()
            index(self.vault, g)
            project(g, self.p)

    def read(self, name):
        return (self.p.views / name).read_text()

    def test_me(self):
        me = self.read('me.md')
        self.assertIn('## Confirmed (1)', me)
        self.assertIn('## Hypotheses (1)', me)
        self.assertIn('The user is a Field CTO at an IT consulting firm.', me)
        self.assertIn('scope: Current professional role.', me)
        self.assertIn('(../../sources/2026-11-08-role.md)', me)
        self.assertIn('(../../identity/profile.md#professional-role)', me)
        self.assertIn('scope: tracked and private', me.splitlines()[2])

    def test_review_queue_and_stale(self):
        queue = self.read('review-queue.md')
        self.assertIn('Prefers short emails to clients.', queue)
        self.assertIn('Keep them short, but always end with the next step.', queue)
        stale = self.read('stale.md')
        self.assertIn('Prefers short emails to clients.', stale)
        self.assertNotIn('Field CTO', stale)

    def test_empty_views_and_report(self):
        self.assertIn('None recorded yet.', self.read('commitments.md'))
        self.assertIn('None recorded yet.', self.read('decisions.md'))
        report = self.read('index-report.md')
        self.assertIn('parsed: 6', report)
        self.assertIn('rejected: 0', report)

    def test_commitments_and_decisions_with_rows(self):
        cs = Changeset(
            [Node('Commitment', 'commitment:send-deck', {'label': 'Send the deck', 'status': 'open', 'owner_known': False, 'scope': 'private'}),
             Node('Decision', 'decision:use-graph', {'label': 'Use a graph', 'date': '2026-11-08', 'scope': 'private'})],
            [Edge('SUPPORTED_BY', 'commitment:send-deck', 'source:2026-11-08-role'),
             Edge('SUPPORTED_BY', 'decision:use-graph', 'source:2026-11-08-role')])
        with Graph.open(self.p) as g:
            g.apply(cs, 'mcp:claude')
            project(g, self.p)
        self.assertIn('**Send the deck** · status: open · source: [sources/2026-11-08-role.md](../../sources/2026-11-08-role.md)',
                      self.read('commitments.md'))
        self.assertIn('**Use a graph** · date: 2026-11-08', self.read('decisions.md'))


if __name__ == '__main__':
    unittest.main()
