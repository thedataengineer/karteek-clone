"""The one owner of the LadybugDB file: open, schema, reads, and the gated write path."""
import fcntl
import json
from datetime import datetime, timezone

import ladybug as lb

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
    def open(cls, p, read_only=False):
        p.graph_dir.mkdir(parents=True, exist_ok=True)
        lock_file = None
        if not read_only:
            lock_file = p.lock.open('a')
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_file.close()
                raise RuntimeError('another duet process holds the graph')
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

    # journal
    def journal(self, result, actor, changeset, when, gate=None, message=''):
        entry = {'ts': when, 'actor': actor, 'result': result, 'gate': gate, 'message': message,
                 'changeset': changeset.to_json() if actor != 'indexer' else changeset.summary()}
        with self.paths.journal.open('a') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
