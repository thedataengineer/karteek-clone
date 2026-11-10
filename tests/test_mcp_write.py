import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from duet.config import paths
from duet.ecl import index
from duet.graph import Graph
from duet.mcp_server import build_server


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

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'
READ = ['claims', 'duet_status', 'get_entity', 'query', 'related', 'search']
WRITE = ['propose_claim', 'record_commitment', 'record_decision', 'record_source']


class McpWriteTests(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp()) / 'vault'
        shutil.copytree(FIXTURE, self.vault)
        self.p = paths(vault=self.vault)
        with Graph.open(self.p) as g:
            g.ensure_schema()
            index(self.vault, g)
        self.server = build_server(self.p)
        self.source = call(self.server, 'record_source', {'ref': 'granola:42', 'date': '2026-11-09'})['id']

    def node(self, node_id):
        with Graph.open(self.p, read_only=True) as graph:
            return graph.node(node_id), graph.neighbors(node_id, None, 'out')

    def test_tool_lists_with_and_without_write_tools(self):
        names = sorted(t.name for t in asyncio.run(self.server.list_tools()))
        self.assertEqual(names, sorted(READ + WRITE))
        read_only = build_server(self.p, read_only=True)
        self.assertEqual(sorted(t.name for t in asyncio.run(read_only.list_tools())), READ)

    def test_record_source_is_private_and_never_a_statement(self):
        (table, props), _ = self.node(self.source)
        self.assertEqual((table, props['kind'], props['scope'], props['provider']), ('Source', 'meeting', 'private', 'granola'))
        with self.assertRaises(RuntimeError) as ctx:
            call(self.server, 'record_source', {'ref': 'chat:1', 'date': '2026-11-09', 'kind': 'statement'})
        self.assertIn('statement', str(ctx.exception))

    def test_propose_claim_is_always_a_hypothesis_about_karteek_with_its_source(self):
        out = call(self.server, 'propose_claim', {'statement': 'Prefers written briefs before meetings', 'source_id': self.source,
                                                  'scope_text': 'Client meetings'})
        (table, props), edges = self.node(out['id'])
        self.assertEqual((table, props['status'], props['owner'], props['scope']), ('Claim', 'hypothesis', 'assistant', 'private'))
        self.assertEqual(sorted((rel, other) for rel, _, other, _ in edges),
                         [('ABOUT', 'person:karteek'), ('SUPPORTED_BY', 'source:ext:granola:42')])
        self.assertIn('Prefers written briefs', (self.vault / 'private/views/review-queue.md').read_text())
        last = json.loads(self.p.journal.read_text().splitlines()[-1])
        self.assertEqual((last['actor'], last['result']), ('mcp:claude', 'applied'))

    def test_a_claim_without_an_existing_source_is_refused_with_the_gate_named(self):
        with self.assertRaises(RuntimeError) as ctx:
            call(self.server, 'propose_claim', {'statement': 'Unsupported', 'source_id': 'source:ext:nope'})
        self.assertIn('integrity', str(ctx.exception))
        last = json.loads(self.p.journal.read_text().splitlines()[-1])
        self.assertEqual((last['actor'], last['result']), ('mcp:claude', 'rejected'))

    def test_a_confirmed_claim_cannot_be_overwritten_through_the_tool(self):
        out = call(self.server, 'propose_claim', {'statement': 'Likes demos', 'source_id': self.source, 'key': 'likes-demos'})
        with Graph.open(self.p) as graph:
            graph.execute("MATCH (c:Claim {id: $id}) SET c.status = 'confirmed'", {'id': out['id']})
        with self.assertRaises(RuntimeError) as ctx:
            call(self.server, 'propose_claim', {'statement': 'Likes demos a lot', 'source_id': self.source, 'key': 'likes-demos'})
        self.assertIn('confirmed', str(ctx.exception))

    def test_decision_and_commitment_carry_source_account_and_stated_owner_only(self):
        decision = call(self.server, 'record_decision', {'summary': 'Go with option B', 'source_id': self.source,
                                                         'date': '2026-11-09', 'account': 'Acme'})
        _, edges = self.node(decision['id'])
        self.assertEqual(sorted((rel, other) for rel, _, other, _ in edges),
                         [('CONCERNS', 'account:acme'), ('SUPPORTED_BY', 'source:ext:granola:42')])
        unowned = call(self.server, 'record_commitment', {'summary': 'Send the estimate', 'source_id': self.source})
        self.assertEqual(self.node(unowned['id'])[0][1]['owner_known'], False)
        owned = call(self.server, 'record_commitment', {'summary': 'Review the draft', 'source_id': self.source,
                                                        'owner_id': 'person:karteek', 'due': '2026-11-20'})
        (_, props), edges = self.node(owned['id'])
        self.assertEqual((props['owner_known'], props['due']), (True, '2026-11-20'))
        self.assertIn(('COMMITTED_BY', 'person:karteek'), [(rel, other) for rel, _, other, _ in edges])
        with self.assertRaises(RuntimeError):
            call(self.server, 'record_commitment', {'summary': 'x', 'source_id': self.source, 'owner_id': 'person:nobody'})


if __name__ == '__main__':
    unittest.main()
