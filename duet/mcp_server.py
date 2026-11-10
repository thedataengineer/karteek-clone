"""MCP tools over the graph: read tools for any agent, and gated write tools that record hypotheses as `mcp:claude`."""
import json
import re
from contextlib import contextmanager
from datetime import date as calendar_date
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from duet.changeset import Changeset, Edge, GateError, Node
from duet.graph import Graph
from duet.parse import slugify
from duet.project import project as run_project
from duet.schema import NODE_TABLES, REL_TABLES

SOURCE_KINDS = ('meeting', 'article', 'external')  # a statement by Karteek is a source page in the vault, never a tool call
KARTEEK = 'person:karteek'
READ_ONLY_NOTE = ('Read-only view of the Karteek Duet knowledge graph. Every Claim carries a status (confirmed, hypothesis, unknown, superseded) '
                  'and SUPPORTED_BY edges to its Source; cite the source locator when you use a claim. Nothing here writes to the graph.')
WRITE_NOTE = ('The Karteek Duet knowledge graph. Every Claim carries a status (confirmed, hypothesis, unknown, superseded) and SUPPORTED_BY '
              'edges to its Source; cite the source locator when you use a claim. The record_* and propose_claim tools write through the '
              'gates: everything they add is private, owned by the assistant, and needs an existing Source. A claim written here is a '
              'hypothesis; only Karteek confirms one, in the vault. Never infer an owner or a due date for a commitment.')

WRITE_WORDS = re.compile(r'\b(CREATE|MERGE|SET|DELETE|DETACH|DROP|COPY|INSTALL|LOAD|ALTER|BEGIN|COMMIT|ROLLBACK|IMPORT|EXPORT|ATTACH|USE)\b', re.I)
READ_START = re.compile(r'^\s*(MATCH|OPTIONAL\s+MATCH|RETURN|WITH|UNWIND|CALL\s+SHOW_)', re.I)
OTHER_CALL = re.compile(r'\bCALL\b(?!\s+SHOW_)', re.I)


def guard_read_only(cypher):
    if WRITE_WORDS.search(cypher) or OTHER_CALL.search(cypher) or not READ_START.match(cypher):
        raise ValueError('read-only: only MATCH/RETURN/WITH/UNWIND/CALL SHOW_* queries are allowed')
    return cypher


def _rows_as_dicts(result):
    columns = result.get_column_names()
    out = []
    while result.has_next():
        out.append(dict(zip(columns, result.get_next())))
    return out


def build_server(p, read_only=False):
    @contextmanager
    def reading():
        """Open the graph read-only for one call, so answers always reflect the last index run."""
        if not p.db.exists():
            raise ToolError('the graph has not been built yet; run `python -m duet index` in the code repository')
        try:
            graph = Graph.open(p, read_only=True)
        except RuntimeError as err:
            raise ToolError(f'the graph is busy or unreadable, try again: {err}') from err
        try:
            yield graph
        finally:
            graph.close()

    server = MCPServer('duet', instructions=READ_ONLY_NOTE if read_only else WRITE_NOTE)

    @server.tool()
    def get_entity(id: str) -> dict[str, Any]:
        """One node by id with its properties and every connection grouped by relation."""
        with reading() as graph:
            found = graph.node(id)
            if not found:
                raise ToolError(f'no node {id}')
            table, props = found
            connections = {}
            for rel, other_table, other_id, locator in graph.neighbors(id, None, 'out'):
                connections.setdefault(rel, []).append({'id': other_id, 'type': other_table, 'locator': locator, 'direction': 'out'})
            for rel, other_table, other_id, locator in graph.neighbors(id, None, 'in'):
                connections.setdefault(rel, []).append({'id': other_id, 'type': other_table, 'locator': locator, 'direction': 'in'})
            return {'id': id, 'type': table, 'props': props, 'connections': connections}

    @server.tool()
    def related(id: str, relation: str, direction: str = 'both', type: str | None = None) -> list[dict[str, Any]]:
        """Nodes connected to id by one relation (see schema relations), optionally filtered by node type."""
        with reading() as graph:
            if relation not in REL_TABLES:
                raise ToolError(f'unknown relation {relation}; one of {sorted(REL_TABLES)}')
            out = []
            for rel, other_table, other_id, locator in graph.neighbors(id, relation, direction):
                if type and other_table != type:
                    continue
                out.append({'id': other_id, 'type': other_table, 'locator': locator})
            return out

    @server.tool()
    def search(text: str, limit: int = 10) -> list[dict[str, Any]]:
        """Case-insensitive substring search over node labels across every table."""
        with reading() as graph:
            rows = graph.rows('MATCH (n) WHERE lower(n.label) CONTAINS lower($q) RETURN label(n), n.id, n.label, n.scope ORDER BY n.id LIMIT $limit',
                              {'q': text, 'limit': int(limit)})
            return [{'type': r[0], 'id': r[1], 'label': r[2], 'scope': r[3]} for r in rows]

    @server.tool()
    def claims(status: str | None = None) -> list[dict[str, Any]]:
        """Claims about Karteek, optionally filtered by status, each with its source locators."""
        with reading() as graph:
            rows = graph.rows('MATCH (c:Claim) OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(s:Source) '
                              'RETURN c.id, c.label, c.status, c.scope_text, c.recorded, c.review_by, c.source_locator, collect(s.defined_in) ORDER BY c.id')
            out = [{'id': r[0], 'label': r[1], 'status': r[2], 'scope_text': r[3], 'recorded': r[4], 'review_by': r[5],
                    'locator': r[6], 'sources': [s for s in r[7] if s]} for r in rows]
            return [c for c in out if status is None or c['status'] == status]

    @server.tool()
    def query(cypher: str) -> list[dict[str, Any]]:
        """Run a read-only Cypher query (MATCH/RETURN/WITH/UNWIND). Writing keywords are refused."""
        with reading() as graph:
            try:  # deliberate errors reach the caller with their reason; the file is also opened read-only
                return _rows_as_dicts(graph.execute(guard_read_only(cypher)))
            except (ValueError, RuntimeError) as err:
                raise ToolError(str(err)) from err

    @server.tool()
    def duet_status() -> dict[str, Any]:
        """Node counts per table and the last index run summary."""
        with reading() as graph:
            last = {}
            if p.index_report.exists():
                report = json.loads(p.index_report.read_text())
                last = {key: len(report.get(key, [])) for key in ('parsed', 'unchanged', 'rejected', 'skipped', 'retired', 'broken_links')}
                last['finished'] = report.get('finished')
            return {'vault': str(p.vault), 'counts': graph.counts(), 'tables': sorted(NODE_TABLES), 'last_index': last}

    if read_only:
        return server

    def commit(nodes, edges):
        """Apply one changeset as mcp:claude and refresh the views. A gate refusal reaches the caller with its reason."""
        try:
            graph = Graph.open(p)
        except RuntimeError as err:
            raise ToolError(f'the graph is busy, try again: {err}') from err
        try:
            graph.ensure_schema()
            # an account or person that is already in the graph is linked to, never restated
            nodes = [n for n in nodes if not (n.table in ('Account', 'Person') and graph.node(n.id))]
            try:
                graph.apply(Changeset(nodes=nodes, edges=edges), 'mcp:claude')
            except GateError as err:
                raise ToolError(f'rejected by the {err.gate} gate: {err}') from err
            run_project(graph, p)
        finally:
            graph.close()
        return {'id': nodes[0].id, 'result': 'applied'}

    def private(label, locator):
        return {'label': ' '.join(label.split()), 'scope': 'private', 'owner': 'assistant', 'source_locator': locator, 'defined_in': ''}

    def node_id(prefix, key, text):
        return f'{prefix}:{slugify(key or text)[:80]}'

    def account_parts(name, src, rel):
        if not name.strip():
            return [], []
        aid = f'account:{slugify(name)}'
        return [Node('Account', aid, dict(private(name, ''), stage=''))], [Edge(rel, src, aid, '')]

    @server.tool()
    def record_source(ref: str, date: str, kind: str = 'meeting', label: str = '') -> dict[str, Any]:
        """Record an external record (a meeting note, an article) so claims, decisions and commitments can cite it.
        ref is the provider's stable id, for example granola:<id>. kind is meeting, article or external."""
        if kind not in SOURCE_KINDS:
            raise ToolError(f'kind must be one of {SOURCE_KINDS}; a statement by Karteek is a source page in the vault, not a tool call')
        provider = ref.split(':', 1)[0].lower() if ':' in ref else ''
        node = Node('Source', f'source:ext:{ref}', dict(private(label or ref, ref), owner='provider', kind=kind, date=date,
                                                        origin=ref, provider=provider))
        return commit([node], [])

    @server.tool()
    def propose_claim(statement: str, source_id: str, scope_text: str = '', about: str = KARTEEK, key: str = '') -> dict[str, Any]:
        """Propose a claim as a hypothesis, supported by an existing Source. about is person:karteek or an account id.
        The claim lands in the review queue; it becomes confirmed only through Karteek's own action in the vault."""
        cid = node_id('claim:mcp', key, statement)
        with reading() as graph:
            stored = graph.node(cid)
        if stored and stored[1].get('status') != 'hypothesis':
            raise ToolError(f'{cid} is {stored[1].get("status")}; a claim that is confirmed, unknown or superseded is changed in the vault, not here')
        nodes = [Node('Claim', cid, dict(private(statement, source_id), status='hypothesis', scope_text=scope_text,
                                         recorded=calendar_date.today().isoformat(), review_by='', exceptions=''))]
        if about == KARTEEK:
            nodes.append(Node('Person', KARTEEK, {'label': 'Karteek', 'scope': 'tracked', 'owner': 'karteek', 'is_karteek': True,
                                                  'role': '', 'defined_in': ''}))
        return commit(nodes, [Edge('SUPPORTED_BY', cid, source_id, source_id), Edge('ABOUT', cid, about, source_id)])

    @server.tool()
    def record_decision(summary: str, source_id: str, date: str = '', rationale: str = '', account: str = '', key: str = '') -> dict[str, Any]:
        """Record a decision found in a source, with the account it concerns when the source names one."""
        did = node_id('decision:mcp', key, summary)
        extra_nodes, extra_edges = account_parts(account, did, 'CONCERNS')
        node = Node('Decision', did, dict(private(summary, source_id), date=date, rationale=rationale, reopen_when=''))
        return commit([node] + extra_nodes, [Edge('SUPPORTED_BY', did, source_id, source_id)] + extra_edges)

    @server.tool()
    def record_commitment(summary: str, source_id: str, due: str = '', owner_id: str = '', account: str = '', key: str = '') -> dict[str, Any]:
        """Record an explicit promise found in a source. Pass owner_id (a Person id) and due only when the source states them;
        leave them empty otherwise and the commitment is stored as owner unknown."""
        cid = node_id('commitment:mcp', key, summary)
        extra_nodes, extra_edges = account_parts(account, cid, 'CONCERNS')
        edges = [Edge('SUPPORTED_BY', cid, source_id, source_id)] + extra_edges
        if owner_id:
            edges.append(Edge('COMMITTED_BY', cid, owner_id, source_id))
        node = Node('Commitment', cid, dict(private(summary, source_id), due=due, status='open', owner_known=bool(owner_id)))
        return commit([node] + extra_nodes, edges)

    return server


def serve(p, read_only=False):
    build_server(p, read_only=read_only).run('stdio')
