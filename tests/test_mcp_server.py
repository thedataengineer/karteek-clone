import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from duet.config import paths
from duet.ecl import index
from duet.graph import Graph
from duet.mcp_server import build_server, guard_read_only

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'
CLAIM = 'claim:identity/profile:professional-role'


def call(server, name, args):
    """Call a tool in-process; a refused or failed call surfaces as RuntimeError with the reason."""
    try:
        result = asyncio.run(server.call_tool(name, args))
    except Exception as err:
        raise RuntimeError(str(err)) from err
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    data = result.structured_content
    return data['result'] if set(data) == {'result'} else data


class McpServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vault = Path(tempfile.mkdtemp()) / 'vault'
        shutil.copytree(FIXTURE, cls.vault)
        cls.p = paths(vault=cls.vault)
        with Graph.open(cls.p) as g:
            g.ensure_schema()
            index(cls.vault, g)
        cls.server = build_server(cls.p)

    def test_tool_names(self):
        names = sorted(t.name for t in asyncio.run(self.server.list_tools()))
        self.assertEqual(names, ['claims', 'duet_status', 'get_entity', 'query', 'related', 'search'])

    def test_get_entity_and_related(self):
        entity = call(self.server, 'get_entity', {'id': CLAIM})
        self.assertEqual(entity['type'], 'Claim')
        self.assertEqual(entity['props']['status'], 'confirmed')
        self.assertEqual(entity['connections']['SUPPORTED_BY'][0]['id'], 'source:2026-11-08-role')
        related = call(self.server, 'related', {'id': CLAIM, 'relation': 'SUPPORTED_BY', 'direction': 'out'})
        self.assertEqual([r['id'] for r in related], ['source:2026-11-08-role'])

    def test_search_claims_status(self):
        self.assertEqual([r['id'] for r in call(self.server, 'search', {'text': 'consulting'})], [CLAIM])
        self.assertEqual(len(call(self.server, 'claims', {'status': 'hypothesis'})), 1)
        status = call(self.server, 'duet_status', {})
        self.assertEqual(status['counts']['Claim'], 2)
        self.assertEqual(status['last_index']['rejected'], 0)

    def test_query_guard(self):
        rows = call(self.server, 'query', {'cypher': 'MATCH (c:Claim) RETURN c.id AS id ORDER BY id'})
        self.assertEqual([r['id'] for r in rows], ['claim:identity/profile:likes-short-emails', CLAIM])
        for bad in ('MATCH (c:Claim) SET c.status = "x" RETURN c', 'CREATE (:Claim {id:"z"})', 'MERGE (c:Claim {id:"z"})',
                    'MATCH (c) DELETE c', 'DROP TABLE Claim', 'COPY Claim FROM "x.csv"', 'INSTALL fts', 'LOAD EXTENSION fts',
                    'ALTER TABLE Claim ADD x STRING', 'BEGIN TRANSACTION', 'COMMIT', 'ROLLBACK'):
            with self.assertRaises(ValueError, msg=bad):
                guard_read_only(bad)
            with self.assertRaises(RuntimeError, msg=bad) as ctx:
                call(self.server, 'query', {'cypher': bad})
            self.assertIn('read-only', str(ctx.exception))
        with self.assertRaises(ValueError):
            guard_read_only('MATCH (n) CALL create_fts_index("Claim", "i", ["label"]) RETURN n')
        self.assertEqual(guard_read_only('CALL SHOW_TABLES() RETURN *'), 'CALL SHOW_TABLES() RETURN *')

    def test_errors_carry_their_reason(self):
        with self.assertRaises(RuntimeError) as ctx:
            call(self.server, 'get_entity', {'id': 'claim:nope'})
        self.assertIn('no node claim:nope', str(ctx.exception))
        with self.assertRaises(RuntimeError) as ctx:
            call(self.server, 'query', {'cypher': 'MATCH (c:Nope) RETURN c'})
        self.assertIn('Nope', str(ctx.exception))

    def test_database_itself_is_read_only(self):
        with Graph.open(self.p, read_only=True) as g:
            with self.assertRaises(RuntimeError):
                g.execute("CREATE (:Source {id:'source:x', label:'X'})")


class FreshnessTests(unittest.TestCase):
    def test_running_server_sees_later_index_runs_and_reports_a_missing_graph(self):
        vault = Path(tempfile.mkdtemp()) / 'vault'
        shutil.copytree(FIXTURE, vault)
        p = paths(vault=vault)
        server = build_server(p)
        with self.assertRaises(RuntimeError) as ctx:
            call(server, 'duet_status', {})
        self.assertIn('python -m duet index', str(ctx.exception))
        with Graph.open(p) as g:
            g.ensure_schema()
            index(vault, g)
        self.assertEqual(call(server, 'duet_status', {})['counts']['Claim'], 2)
        profile = vault / 'identity' / 'profile.md'
        profile.write_text(profile.read_text() + '\n## New one\n\n- **Claim:** Added later.\n- **Status:** hypothesis\n'
                           '- **Evidence:** [Role](../sources/2026-11-08-role.md)\n')
        with Graph.open(p) as g:
            index(vault, g)
        self.assertEqual(call(server, 'duet_status', {})['counts']['Claim'], 3)


if __name__ == '__main__':
    unittest.main()
