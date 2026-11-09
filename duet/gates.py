"""The Constrain and Validate steps: a changeset is applied whole or rejected whole."""
from collections import defaultdict

from duet.changeset import GateError
from duet.schema import ACTORS, EVIDENCE_TABLES, NODE_TABLES, REL_TABLES, STATUSES


def run_gates(cs, graph, actor):
    if actor not in ACTORS:
        raise GateError('actor', f'{actor!r} may not write; allowed: {", ".join(ACTORS)}')

    ids = [n.id for n in cs.nodes]
    if len(set(ids)) != len(ids):
        raise GateError('integrity', 'duplicate node id in changeset')
    in_cs = {n.id: n for n in cs.nodes}

    def table_of(node_id):
        if node_id in in_cs:
            return in_cs[node_id].table
        found = graph.node(node_id)
        return found[0] if found else None

    def props_of(node_id):
        if node_id in in_cs:
            return in_cs[node_id].props
        found = graph.node(node_id)
        return found[1] if found else {}

    for n in cs.nodes:
        if n.table not in NODE_TABLES:
            raise GateError('integrity', f'unknown table {n.table}')
    for e in cs.edges:
        a, b = table_of(e.src), table_of(e.dst)
        if a is None or b is None:
            raise GateError('integrity', f'{e.rel} {e.src} -> {e.dst}: endpoint does not exist')
        if (a, b) not in REL_TABLES.get(e.rel, []):
            raise GateError('integrity', f'{e.rel} does not connect {a} to {b}')

    unlinked = set(cs.unlink)
    for node_id in cs.unlink:
        if node_id not in in_cs:
            raise GateError('integrity', f'unlink {node_id}: a node may only be unlinked in a changeset that restates it')

    def stored_out(node_id, rel):
        """Outgoing edges already in the graph; none survive for a node being unlinked."""
        return [] if node_id in unlinked else [t[2] for t in graph.neighbors(node_id, rel, 'out')]

    supported = defaultdict(list)
    corrected = set()
    committed = set()
    for e in cs.edges:
        if e.rel == 'SUPPORTED_BY':
            supported[e.src].append(e.dst)
        elif e.rel == 'CORRECTS':
            corrected.add(e.dst)
        elif e.rel == 'COMMITTED_BY':
            committed.add(e.src)

    for n in cs.nodes:
        if n.table in EVIDENCE_TABLES:
            sources = supported.get(n.id) or stored_out(n.id, 'SUPPORTED_BY')
            if not sources:
                raise GateError('evidence', f'{n.id} has no SUPPORTED_BY source')
            if n.table == 'Claim':
                status = n.props.get('status')
                if status not in STATUSES:
                    raise GateError('status', f'{n.id}: status {status!r} not in {STATUSES}')
                if status == 'confirmed':
                    kinds = {props_of(s).get('kind') for s in sources}
                    has_feedback = n.id in corrected or bool(graph.neighbors(n.id, 'CORRECTS', 'in'))
                    if 'statement' not in kinds and not has_feedback:
                        raise GateError('status', f'{n.id}: confirmed needs a statement source or a correction')
        if n.table == 'Commitment':
            owner_known = n.props.get('owner_known')
            if not isinstance(owner_known, bool):
                raise GateError('commitment', f'{n.id}: owner_known must be true or false')
            has_owner = n.id in committed or bool(stored_out(n.id, 'COMMITTED_BY'))
            if owner_known != has_owner:
                raise GateError('commitment', f'{n.id}: owner_known={owner_known} but COMMITTED_BY present={has_owner}')

    def scope_of(node_id):
        return props_of(node_id).get('scope') or 'tracked'

    for e in cs.edges:
        if scope_of(e.src) == 'tracked' and scope_of(e.dst) == 'private':
            raise GateError('scope', f'{e.rel} {e.src} -> {e.dst}: tracked node may not link to private node')
