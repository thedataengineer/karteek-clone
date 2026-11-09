import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
