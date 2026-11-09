import json
import tempfile
import unittest
from pathlib import Path

from duet.changeset import Changeset, Edge, GateError, Node
from duet.config import paths
from duet.graph import Graph
from duet.schema import NODE_TABLES, REL_TABLES


def fresh():
    folder = tempfile.mkdtemp()
    (Path(folder) / 'AGENTS.md').write_text('# t\n')
    return paths(vault=folder)


class GraphStoreTests(unittest.TestCase):
    def test_schema_is_complete_and_idempotent(self):
        p = fresh()
        with Graph.open(p) as g:
            g.ensure_schema()
            g.ensure_schema()
            tables = set(g.tables())
        self.assertTrue(set(NODE_TABLES) <= tables)
        self.assertTrue(set(REL_TABLES) <= tables)

    def test_node_and_neighbors(self):
        p = fresh()
        with Graph.open(p) as g:
            g.ensure_schema()
            self.assertIsNone(g.node('claim:x'))
            g.execute("CREATE (:Source {id:'source:s', label:'S', kind:'statement', scope:'tracked', history:[]})")
            g.execute("CREATE (:Claim {id:'claim:x', label:'X', status:'confirmed', scope:'tracked', history:[]})")
            g.execute("MATCH (c:Claim {id:'claim:x'}),(s:Source {id:'source:s'}) CREATE (c)-[:SUPPORTED_BY {locator:'L1'}]->(s)")
            table, props = g.node('claim:x')
            self.assertEqual(table, 'Claim')
            self.assertEqual(props['status'], 'confirmed')
            self.assertFalse(any(k.startswith('_') for k in props))
            self.assertEqual(g.neighbors('claim:x', 'SUPPORTED_BY', 'out'), [('SUPPORTED_BY', 'Source', 'source:s', 'L1')])
            self.assertEqual(g.neighbors('source:s', None, 'in'), [('SUPPORTED_BY', 'Claim', 'claim:x', 'L1')])
            self.assertEqual(g.counts()['Claim'], 1)

    def test_lock_excludes_second_writer_but_not_reader(self):
        p = fresh()
        with Graph.open(p) as g:
            g.ensure_schema()
            with self.assertRaises(RuntimeError):
                Graph.open(p)
        with Graph.open(p) as g:
            g.ensure_schema()
            with Graph.open(p, read_only=True) as r:
                self.assertIn('Claim', r.tables())


def statement_and_claim(status='confirmed'):
    return Changeset(
        [Node('Source', 'source:s', {'label': 'S', 'kind': 'statement'}),
         Node('Claim', 'claim:c', {'label': 'C', 'status': status, 'defined_in': 'identity/profile.md'})],
        [Edge('SUPPORTED_BY', 'claim:c', 'source:s', 'L1')])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.p = fresh()
        self.g = Graph.open(self.p)
        self.g.ensure_schema()

    def tearDown(self):
        self.g.close()

    def test_create_sets_defaults_and_journal_summary(self):
        self.g.apply(statement_and_claim(), 'indexer', now='2026-11-08T10:00:00+00:00')
        table, props = self.g.node('claim:c')
        self.assertEqual((props['scope'], props['owner'], props['history']), ('tracked', 'karteek', []))
        self.assertEqual(props['created'], props['updated'])
        entries = self.g.journal_entries()
        self.assertEqual(entries[-1]['result'], 'applied')
        self.assertEqual(entries[-1]['changeset'], {'nodes': ['source:s', 'claim:c'], 'edges': 1, 'retire': []})

    def test_update_keeps_history_and_merges_edge(self):
        self.g.apply(statement_and_claim(), 'indexer', now='2026-11-08T10:00:00+00:00')
        cs = statement_and_claim(status='superseded')
        cs.edges[0].locator = 'L2'
        self.g.apply(cs, 'indexer', now='2026-11-09T10:00:00+00:00')
        _, props = self.g.node('claim:c')
        self.assertEqual(props['status'], 'superseded')
        self.assertEqual(props['updated'], '2026-11-09T10:00:00+00:00')
        self.assertEqual([json.loads(h) for h in props['history']], [{'status': 'confirmed'}])
        self.assertEqual(self.g.neighbors('claim:c', 'SUPPORTED_BY', 'out'), [('SUPPORTED_BY', 'Source', 'source:s', 'L2')])

    def test_retire_removes_node_and_edges(self):
        self.g.apply(statement_and_claim(), 'indexer')
        self.g.apply(Changeset(retire=['claim:c']), 'indexer')
        self.assertIsNone(self.g.node('claim:c'))
        self.assertEqual(self.g.neighbors('source:s'), [])

    def test_rejection_changes_nothing_and_is_journaled(self):
        before = self.g.counts()
        with self.assertRaises(GateError):
            self.g.apply(Changeset([Node('Claim', 'claim:c', {'label': 'C', 'status': 'confirmed'})]), 'mcp:claude')
        self.assertEqual(self.g.counts(), before)
        entry = self.g.journal_entries()[-1]
        self.assertEqual((entry['result'], entry['gate'], entry['actor']), ('rejected', 'evidence', 'mcp:claude'))
        self.assertIn('nodes', entry['changeset'])
        self.assertEqual(entry['changeset']['nodes'][0]['id'], 'claim:c')

    def test_unlink_replaces_outgoing_edges(self):
        self.g.apply(statement_and_claim(), 'indexer')
        other = Changeset(
            [Node('Source', 'source:t', {'label': 'T', 'kind': 'statement'}),
             Node('Claim', 'claim:c', {'label': 'C', 'status': 'confirmed', 'defined_in': 'identity/profile.md'})],
            [Edge('SUPPORTED_BY', 'claim:c', 'source:t', 'L9')], unlink=['claim:c'])
        self.g.apply(other, 'indexer')
        self.assertEqual(self.g.neighbors('claim:c', 'SUPPORTED_BY', 'out'), [('SUPPORTED_BY', 'Source', 'source:t', 'L9')])
        self.assertEqual(self.g.neighbors('source:s'), [])


if __name__ == '__main__':
    unittest.main()
