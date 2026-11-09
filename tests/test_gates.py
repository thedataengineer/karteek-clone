import tempfile
import unittest
from pathlib import Path

from duet.changeset import Changeset, Edge, GateError, Node
from duet.config import paths
from duet.gates import run_gates
from duet.graph import Graph


def fresh_graph():
    folder = tempfile.mkdtemp()
    (Path(folder) / 'AGENTS.md').write_text('# t\n')
    g = Graph.open(paths(vault=folder))
    g.ensure_schema()
    return g


def source(sid='source:s', kind='statement'):
    return Node('Source', sid, {'label': 'S', 'kind': kind, 'scope': 'tracked'})


def claim(cid='claim:c', status='confirmed'):
    return Node('Claim', cid, {'label': 'C', 'status': status, 'scope': 'tracked'})


class GateTests(unittest.TestCase):
    def setUp(self):
        self.g = fresh_graph()

    def tearDown(self):
        self.g.close()

    def gate(self, cs, actor='indexer'):
        with self.assertRaises(GateError) as ctx:
            run_gates(cs, self.g, actor)
        return ctx.exception.gate

    def test_valid_changeset_passes(self):
        cs = Changeset([source(), claim()], [Edge('SUPPORTED_BY', 'claim:c', 'source:s', 'L1')])
        run_gates(cs, self.g, 'indexer')

    def test_round_trip_and_summary(self):
        cs = Changeset([source()], [Edge('SUPPORTED_BY', 'claim:c', 'source:s')], ['old:1'])
        self.assertEqual(Changeset.from_json(cs.to_json()), cs)
        self.assertEqual(cs.summary(), {'nodes': ['source:s'], 'edges': 1, 'retire': ['old:1']})

    def test_actor(self):
        self.assertEqual(self.gate(Changeset([source()]), actor='mcp:codex'), 'actor')

    def test_integrity(self):
        self.assertEqual(self.gate(Changeset([Node('Widget', 'w:1')])), 'integrity')
        self.assertEqual(self.gate(Changeset([source(), source()])), 'integrity')
        self.assertEqual(self.gate(Changeset([claim()], [Edge('SUPPORTED_BY', 'claim:c', 'source:missing')])), 'integrity')
        self.assertEqual(self.gate(Changeset([source(), claim()], [Edge('ATTENDED', 'claim:c', 'source:s')])), 'integrity')

    def test_evidence(self):
        self.assertEqual(self.gate(Changeset([claim()])), 'evidence')

    def test_status(self):
        cs = Changeset([source(), claim(status='maybe')], [Edge('SUPPORTED_BY', 'claim:c', 'source:s')])
        self.assertEqual(self.gate(cs), 'status')
        cs = Changeset([source(kind='meeting'), claim()], [Edge('SUPPORTED_BY', 'claim:c', 'source:s')])
        self.assertEqual(self.gate(cs), 'status')
        cs = Changeset([source(kind='meeting'), claim(status='hypothesis')], [Edge('SUPPORTED_BY', 'claim:c', 'source:s')])
        run_gates(cs, self.g, 'indexer')

    def test_existing_statement_source_in_graph_passes(self):
        self.g.execute("CREATE (:Source {id:'source:s', label:'S', kind:'statement', scope:'tracked', history:[]})")
        cs = Changeset([claim()], [Edge('SUPPORTED_BY', 'claim:c', 'source:s')])
        run_gates(cs, self.g, 'indexer')

    def test_feedback_correction_satisfies_confirmed(self):
        fb = Node('Feedback', 'feedback:1', {'label': 'F', 'scope': 'tracked', 'applied': True})
        cs = Changeset([source(kind='meeting'), claim(), fb],
                       [Edge('SUPPORTED_BY', 'claim:c', 'source:s'), Edge('CORRECTS', 'feedback:1', 'claim:c')])
        run_gates(cs, self.g, 'indexer')

    def test_commitment(self):
        person = Node('Person', 'person:karteek', {'label': 'Karteek', 'scope': 'tracked', 'is_karteek': True})
        base = [source(), person]
        cs = Changeset(base + [Node('Commitment', 'commitment:1', {'label': 'Send deck', 'scope': 'tracked'})],
                       [Edge('SUPPORTED_BY', 'commitment:1', 'source:s')])
        self.assertEqual(self.gate(cs), 'commitment')
        cs = Changeset(base + [Node('Commitment', 'commitment:1', {'label': 'Send deck', 'scope': 'tracked', 'owner_known': True})],
                       [Edge('SUPPORTED_BY', 'commitment:1', 'source:s')])
        self.assertEqual(self.gate(cs), 'commitment')
        cs = Changeset(base + [Node('Commitment', 'commitment:1', {'label': 'Send deck', 'scope': 'tracked', 'owner_known': True})],
                       [Edge('SUPPORTED_BY', 'commitment:1', 'source:s'), Edge('COMMITTED_BY', 'commitment:1', 'person:karteek')])
        run_gates(cs, self.g, 'indexer')

    def test_scope(self):
        private_doc = Node('Document', 'doc:private/accounts/acme/context.md', {'label': 'Acme', 'scope': 'private'})
        tracked_doc = Node('Document', 'doc:identity/profile.md', {'label': 'Profile', 'scope': 'tracked'})
        cs = Changeset([private_doc, tracked_doc], [Edge('LINKS_TO', 'doc:identity/profile.md', 'doc:private/accounts/acme/context.md')])
        self.assertEqual(self.gate(cs), 'scope')
        cs = Changeset([private_doc, tracked_doc], [Edge('LINKS_TO', 'doc:private/accounts/acme/context.md', 'doc:identity/profile.md')])
        run_gates(cs, self.g, 'indexer')


if __name__ == '__main__':
    unittest.main()
