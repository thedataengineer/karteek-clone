"""The one owner of the LadybugDB file: open, schema, reads, and the gated write path."""
import fcntl
import json
from datetime import datetime, timezone

import ladybug as lb

from duet.changeset import GateError
from duet.gates import run_gates
from duet.schema import NODE_TABLES, ddl


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Graph:
    def __init__(self, p, db, conn, lock_file):
        self.paths = p
        self._db = db
        self._conn = conn
        self._lock_file = lock_file

    @classmethod
    def open(cls, p, read_only=False, fresh=False):
        """Open the graph. A writer holds the lock file; fresh=True deletes the database under that lock first."""
        p.graph_dir.mkdir(parents=True, exist_ok=True)
        lock_file = None
        if not read_only:
            lock_file = p.lock.open('a')
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.close()
                raise RuntimeError('another duet process holds the graph')
            if fresh:
                for stale in [p.db, *p.graph_dir.glob(p.db.name + '.*')]:
                    if stale.exists():
                        stale.unlink()
        db = lb.Database(str(p.db), read_only=read_only)
        return cls(p, db, lb.Connection(db), lock_file)

    def close(self):
        self._conn.close()
        self._db.close()
        if self._lock_file:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # reads
    def execute(self, statement, params=None):
        return self._conn.execute(statement, parameters=params) if params else self._conn.execute(statement)

    def rows(self, statement, params=None):
        result = self.execute(statement, params)
        out = []
        while result.has_next():
            out.append(result.get_next())
        return out

    def ensure_schema(self):
        for statement in ddl():
            self.execute(statement)

    def tables(self):
        return [row[1] for row in self.rows('CALL SHOW_TABLES() RETURN *')]

    def node(self, node_id):
        found = self.rows('MATCH (n {id: $id}) RETURN n', {'id': node_id})
        if not found:
            return None
        raw = found[0][0]
        return raw['_LABEL'], {k: v for k, v in raw.items() if not k.startswith('_')}

    def neighbors(self, node_id, rel=None, direction='both'):
        out = []
        if direction in ('out', 'both'):
            out += self.rows('MATCH (n {id: $id})-[r]->(m) RETURN label(r), label(m), m.id, r.locator ORDER BY m.id', {'id': node_id})
        if direction in ('in', 'both'):
            out += self.rows('MATCH (n {id: $id})<-[r]-(m) RETURN label(r), label(m), m.id, r.locator ORDER BY m.id', {'id': node_id})
        tuples = [tuple(row) for row in out]
        return [t for t in tuples if rel is None or t[0] == rel]

    def counts(self):
        return {table: self.rows(f'MATCH (n:{table}) RETURN count(n)')[0][0] for table in NODE_TABLES}

    # the one write path
    def apply(self, changeset, actor, now=None, result='applied'):
        """Gate, write and journal one changeset. `result` is the journal label; rebuild passes 'replayed'."""
        when = now or now_iso()
        try:
            run_gates(changeset, self, actor)
        except GateError as err:
            self.journal('rejected', actor, changeset, when, gate=err.gate, message=str(err))
            raise
        tables = {n.id: n.table for n in changeset.nodes}
        self.execute('BEGIN TRANSACTION')
        try:
            for node_id in changeset.retire:
                self.execute('MATCH (n {id: $id}) DETACH DELETE n', {'id': node_id})
            for node in changeset.nodes:
                self._upsert_node(node, when)
            for node_id in changeset.unlink:
                self.execute('MATCH (n {id: $id})-[r]->() DELETE r', {'id': node_id})
            for edge in changeset.edges:
                src_table = tables.get(edge.src) or self.node(edge.src)[0]
                dst_table = tables.get(edge.dst) or self.node(edge.dst)[0]
                self._upsert_edge(edge, src_table, dst_table, when)
            self.execute('COMMIT')
        except Exception:
            self.execute('ROLLBACK')
            raise
        self.journal(result, actor, changeset, when)

    def _upsert_node(self, node, when):
        props = dict(node.props)
        props.setdefault('scope', 'tracked')
        props.setdefault('owner', 'karteek')
        props.pop('id', None)
        for reserved in ('created', 'updated', 'history'):
            props.pop(reserved, None)
        existing = self.node(node.id)
        if existing is None:
            keys = ', '.join(f'{k}: ${k}' for k in props)
            self.execute(f'CREATE (n:{node.table} {{id: $id, created: $now, updated: $now, history: [], {keys}}})',
                         dict(props, id=node.id, now=when))
            return
        old = existing[1]
        changed = {k: old.get(k) for k in props if old.get(k) != props[k]}
        sets = ', '.join(f'n.{k} = ${k}' for k in props)
        self.execute(f'MATCH (n:{node.table} {{id: $id}}) SET {sets}, n.updated = $now', dict(props, id=node.id, now=when))
        if changed:
            self.execute(f'MATCH (n:{node.table} {{id: $id}}) SET n.history = list_append(n.history, $h)',
                         {'id': node.id, 'h': json.dumps(changed, sort_keys=True, default=str)})

    def _upsert_edge(self, edge, src_table, dst_table, when):
        self.execute(f'MATCH (a:{src_table} {{id: $a}}), (b:{dst_table} {{id: $b}}) '
                     f'MERGE (a)-[r:{edge.rel}]->(b) SET r.locator = $locator, r.created = coalesce(r.created, $now)',
                     {'a': edge.src, 'b': edge.dst, 'locator': edge.locator or '', 'now': when})

    def journal_entries(self):
        if not self.paths.journal.exists():
            return []
        return [json.loads(line) for line in self.paths.journal.read_text().splitlines() if line.strip()]

    # journal
    def journal(self, result, actor, changeset, when, gate=None, message=''):
        entry = {'ts': when, 'actor': actor, 'result': result, 'gate': gate, 'message': message,
                 'changeset': changeset.to_json() if actor != 'indexer' else changeset.summary()}
        with self.paths.journal.open('a') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
