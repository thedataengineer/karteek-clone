"""Read-only MCP tools over the graph, for Claude Code, Claudian and the runner."""
import json
import re
from contextlib import contextmanager
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from duet.graph import Graph
from duet.schema import NODE_TABLES, REL_TABLES

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


def build_server(p):
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

    server = MCPServer('duet', instructions=(
        'Read-only view of the Karteek Duet knowledge graph. Every Claim carries a status (confirmed, hypothesis, unknown, superseded) '
        'and SUPPORTED_BY edges to its Source; cite the source locator when you use a claim. Nothing here writes to the graph.'))

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

    return server


def serve(p):
    build_server(p).run('stdio')
