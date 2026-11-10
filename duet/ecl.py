"""Entity-Context-Linking: vault pages in, gated changesets out, one report per run.

A run has two passes. Pass one applies what each changed page defines (its Document, Source,
Concept, Claim and Feedback nodes with the edges that make them valid), source pages first so a
claim is never gated before the source it cites exists. Pass two merges the cross-page edges
(LINKS_TO, MENTIONS, CORRECTS, SUPERSEDES) once every definition is in place. Daily notes under
private/duet/ are not pages: `daily.read_back` reads their status lines and drafts at the end.
"""
import json
from pathlib import Path

from duet import daily
from duet.changeset import Changeset, Edge, GateError, Node
from duet.graph import now_iso
from duet.parse import parse_page, slugify

EXCLUDED_PREFIXES = ('.obsidian/', '.git/', 'private/views/', 'private/graph/', 'private/duet/')
TEMPLATE_PREFIX = 'templates/'  # templates hold placeholder fields: Documents only, never entities
INTRINSIC = ('SUPPORTED_BY', 'ABOUT', 'DERIVED_FROM', 'ATTRIBUTED_TO')  # edges a definition needs to pass the gates
KARTEEK = 'person:karteek'


def iter_pages(vault):
    vault = Path(vault)
    out = []
    for path in sorted(vault.rglob('*.md')):
        rel = path.relative_to(vault).as_posix()
        if rel.startswith(EXCLUDED_PREFIXES) or '/.' in f'/{rel}':
            continue
        out.append(rel)
    return out


def scope_of(rel_path):
    return 'private' if rel_path.startswith('private/') else 'tracked'


def doc_id(rel_path):
    return f'doc:{rel_path}'


def stem(rel_path):
    return rel_path[:-3] if rel_path.endswith('.md') else rel_path


def claim_id(rel_path, slug):
    return f'claim:{stem(rel_path)}:{slug}'


def source_id(rel_path):
    return f'source:{Path(rel_path).stem}'


def is_template(rel_path):
    return rel_path.startswith(TEMPLATE_PREFIX)


def defines_source(page):
    return page.frontmatter.get('type', '') == 'personal-source' and page.path.startswith('sources/')


def claims_of(page):
    return [] if is_template(page.path) else page.claims


def _common(page, label, locator=None):
    return {'label': label, 'scope': scope_of(page.path), 'owner': 'karteek',
            'source_locator': locator or page.path, 'defined_in': page.path}


def document_node(page):
    return Node('Document', doc_id(page.path), dict(
        _common(page, page.title), path=page.path, hash=page.hash,
        frontmatter_type=page.frontmatter.get('type', '')))


def documents_changeset(pages):
    return Changeset(nodes=[document_node(page) for page in pages])


def _person(person_id, label, is_karteek, role, owner):
    # People are shared by many pages, so no page owns them: defined_in stays empty and they are never retired.
    return Node('Person', person_id, {'label': label, 'scope': 'tracked', 'owner': owner,
                                      'is_karteek': is_karteek, 'role': role, 'defined_in': ''})


def page_changeset(page, known_claims, known_docs):
    """Everything a page defines besides its Document node, plus its edges."""
    cs = Changeset()
    template = is_template(page.path)
    kind = '' if template else page.frontmatter.get('type', '')
    if defines_source(page):
        cs.nodes.append(Node('Source', source_id(page.path), dict(
            _common(page, page.title), kind='statement', date=page.frontmatter.get('date', ''),
            origin=page.frontmatter.get('origin', ''), provider='')))
    if kind == 'knowledge':
        author = page.frontmatter.get('author', '')
        cid = f'concept:{Path(page.path).stem}'
        cs.nodes.append(Node('Concept', cid, dict(_common(page, page.title), author=author, owner=author or 'unknown')))
        cs.edges.append(Edge('DERIVED_FROM', cid, doc_id(page.path), page.path))
        if author:
            pid = f'person:{slugify(author)}'
            cs.nodes.append(_person(pid, author, False, 'author', author))
            cs.edges.append(Edge('ATTRIBUTED_TO', cid, pid, page.path))
    if claims_of(page):
        cs.nodes.append(_person(KARTEEK, 'Karteek', True, '', 'karteek'))
    for block in claims_of(page):
        cid = claim_id(page.path, block.slug)
        locator = f'{page.path}#{block.slug}'
        cs.nodes.append(Node('Claim', cid, dict(
            _common(page, block.fields.get('claim', block.heading), locator),
            status=(block.fields.get('status') or 'unknown').split()[0].lower(),
            scope_text=block.fields.get('scope', ''), recorded=block.fields.get('recorded', ''),
            review_by=block.fields.get('review by', ''), exceptions=block.fields.get('exceptions / conflicts', ''))))
        cs.edges.append(Edge('ABOUT', cid, KARTEEK, locator))
        for link in block.evidence_links:
            if link.target.startswith('sources/'):
                cs.edges.append(Edge('SUPPORTED_BY', cid, source_id(link.target), link.fragment or link.target))
        for link in block.supersedes_links:
            target = claim_id(link.target, link.fragment or '')
            if target in known_claims:
                cs.edges.append(Edge('SUPERSEDES', cid, target, locator))
    for n, fb in enumerate([] if template else page.feedback, start=1):
        fid = f'feedback:{stem(page.path)}:{n}'
        cs.nodes.append(Node('Feedback', fid, dict(
            _common(page, fb.text[:80] or f'Feedback {n}', f'{page.path}:{fb.line}'),
            date=fb.date, scope_text=fb.scope, applied=fb.applied)))
        if fb.claim_target and fb.claim_target[1]:
            target = claim_id(fb.claim_target[0], fb.claim_target[1])
            if target in known_claims:
                cs.edges.append(Edge('CORRECTS', fid, target, f'{page.path}:{fb.line}'))
    seen = set()
    for link in page.links:
        if not link.target or link.target == page.path:
            continue
        if link.target in known_docs and ('LINKS_TO', link.target) not in seen:
            cs.edges.append(Edge('LINKS_TO', doc_id(page.path), doc_id(link.target), f'{page.path}:{link.line}'))
            seen.add(('LINKS_TO', link.target))
        if link.fragment:
            target = claim_id(link.target, link.fragment)
            if target in known_claims and ('MENTIONS', target) not in seen:
                cs.edges.append(Edge('MENTIONS', doc_id(page.path), target, f'{page.path}:{link.line}'))
                seen.add(('MENTIONS', target))
    unique = {}
    for node in cs.nodes:  # an author who is also Karteek would otherwise appear twice
        unique.setdefault(node.id, node)
    cs.nodes = list(unique.values())
    return cs


def _defined_in(graph, rel_path):
    return [row[0] for row in graph.rows('MATCH (n) WHERE n.defined_in = $p RETURN n.id', {'p': rel_path})]


def _mergeable(graph, edge, page, report):
    """A cross-page edge is merged only when both ends exist and it does not point from tracked into private."""
    src, dst = graph.node(edge.src), graph.node(edge.dst)
    if src is None or dst is None:
        return False
    if (src[1].get('scope') or 'tracked') == 'tracked' and (dst[1].get('scope') or 'tracked') == 'private':
        report['skipped_edges'].append({'path': page.path, 'edge': f'{edge.rel} {edge.src} -> {edge.dst}',
                                        'reason': 'a tracked node may not link to a private node'})
        return False
    return True


def index(vault, graph, force=False):
    vault = Path(vault)
    p = graph.paths
    state = json.loads(p.index_state.read_text()) if p.index_state.exists() else {}
    report = {'started': now_iso(), 'parsed': [], 'unchanged': [], 'skipped': [], 'rejected': [], 'retired': [],
              'broken_links': [], 'skipped_edges': []}

    pages, new_state = [], {}
    for rel in iter_pages(vault):
        try:
            pages.append(parse_page(vault, rel))
        except Exception as err:  # unreadable page: keep its nodes, report it
            report['skipped'].append({'path': rel, 'reason': str(err)})
            if rel in state:
                new_state[rel] = state[rel]
    by_path = {page.path: page for page in pages}
    known_docs = set(by_path)
    known_claims = {claim_id(page.path, c.slug) for page in pages for c in claims_of(page)}
    skipped_paths = {item['path'] for item in report['skipped']}
    for page in pages:  # every run lists every broken link, whether or not its page changed
        for link in page.links:
            if link.target and link.target not in known_docs:
                report['broken_links'].append({'path': page.path, 'line': link.line, 'target': link.target})

    for rel in state:  # pages deleted since the last run
        if rel not in by_path and rel not in skipped_paths:
            retire = _defined_in(graph, rel)
            if retire:
                graph.apply(Changeset(retire=retire), 'indexer')
            report['retired'] += retire

    changed = [page for page in pages if force or state.get(page.path) != page.hash]
    changed_paths = {page.path for page in changed}
    for page in pages:
        if page.path not in changed_paths:
            report['unchanged'].append(page.path)
            new_state[page.path] = page.hash
    if changed:  # every Document first, so a rejected page still resolves as a link target
        graph.apply(documents_changeset(changed), 'indexer')

    # pass one: definitions, source pages first
    defined, rejected_paths = [], set()
    for page in sorted(changed, key=lambda pg: (0 if defines_source(pg) else 1, pg.path)):
        full = page_changeset(page, known_claims, known_docs)
        owned = [n.id for n in full.nodes if n.props.get('defined_in') == page.path]
        keep = set(owned) | {doc_id(page.path)}
        definition = Changeset(
            nodes=[document_node(page)] + full.nodes,
            edges=[e for e in full.edges if e.rel in INTRINSIC],
            retire=[node_id for node_id in _defined_in(graph, page.path) if node_id not in keep],
            unlink=[doc_id(page.path)] + owned)  # a re-indexed page drops the edges its last version asserted
        try:
            graph.apply(definition, 'indexer')
        except GateError as err:
            report['rejected'].append({'path': page.path, 'gate': err.gate, 'message': str(err)})
            rejected_paths.add(page.path)
            continue
        report['retired'] += definition.retire
        defined.append((page, [e for e in full.edges if e.rel not in INTRINSIC]))

    # pass two: cross-page edges of the changed pages, and of unchanged pages that link to a changed one
    dependents = [page for page in pages if page.path not in changed_paths
                  and any(link.target in changed_paths for link in page.links)]
    for page in dependents:
        full = page_changeset(page, known_claims, known_docs)
        defined.append((page, [e for e in full.edges if e.rel not in INTRINSIC]))
    for page, cross in defined:
        edges = [e for e in cross if _mergeable(graph, e, page, report)]
        try:
            if edges:
                graph.apply(Changeset(edges=edges), 'indexer')
        except GateError as err:
            report['rejected'].append({'path': page.path, 'gate': err.gate, 'message': str(err)})
            rejected_paths.add(page.path)
            new_state.pop(page.path, None)  # a failed dependent is retried on the next run
            continue
        if page.path in changed_paths:
            report['parsed'].append(page.path)
            new_state[page.path] = page.hash

    report['parsed'].sort()
    report['notes'] = daily.read_back(graph, p)  # status and draft edits Karteek made in the daily notes
    report['finished'] = now_iso()
    p.graph_dir.mkdir(parents=True, exist_ok=True)
    p.index_state.write_text(json.dumps(new_state, indent=2, sort_keys=True) + '\n')
    p.index_report.write_text(json.dumps(report, indent=2) + '\n')
    return report
