"""The unit of writing: nodes to upsert, edges to merge, node ids to retire, node ids to unlink."""
from dataclasses import asdict, dataclass, field


class GateError(Exception):
    def __init__(self, gate, message):
        super().__init__(f'{gate}: {message}')
        self.gate = gate


@dataclass
class Node:
    table: str
    id: str
    props: dict = field(default_factory=dict)


@dataclass
class Edge:
    rel: str
    src: str
    dst: str
    locator: str = ''


@dataclass
class Changeset:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    retire: list = field(default_factory=list)
    unlink: list = field(default_factory=list)  # ids whose outgoing edges are dropped before the merge

    def to_json(self):
        return {'nodes': [asdict(n) for n in self.nodes], 'edges': [asdict(e) for e in self.edges], 'retire': list(self.retire),
                'unlink': list(self.unlink)}

    @classmethod
    def from_json(cls, data):
        return cls(nodes=[Node(**n) for n in data.get('nodes', [])],
                   edges=[Edge(**e) for e in data.get('edges', [])],
                   retire=list(data.get('retire', [])),
                   unlink=list(data.get('unlink', [])))

    def summary(self):
        out = {'nodes': [n.id for n in self.nodes], 'edges': len(self.edges), 'retire': list(self.retire)}
        if self.unlink:
            out['unlink'] = len(self.unlink)
        return out
