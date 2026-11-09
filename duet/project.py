"""Render link-dense Markdown views of the graph into private/views/."""
import json
import posixpath
from datetime import date

from duet.graph import now_iso

VIEWS_DIR = 'private/views'


def rel(target):
    """Vault-relative target as a link from private/views/."""
    return posixpath.relpath(target, VIEWS_DIR)


def header(title, p):
    return [f'# {title}', '', f'Generated {now_iso()} by `python -m duet project` · scope: tracked and private · '
            'every line links its claim and its source. Edit the linked pages, not this file.', '']


def claim_rows(graph):
    rows = graph.rows(
        'MATCH (c:Claim) OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(s:Source) '
        'RETURN c.id, c.label, c.status, c.scope_text, c.recorded, c.review_by, c.source_locator, c.scope, collect(s.defined_in) '
        'ORDER BY c.recorded, c.id')
    return [dict(id=r[0], label=r[1], status=r[2], scope_text=r[3], recorded=r[4], review_by=r[5], locator=r[6], scope=r[7],
                 sources=[s for s in r[8] if s]) for r in rows]


def claim_line(c):
    sources = ' '.join(f'[{s}]({rel(s)})' for s in c['sources']) or 'no source'
    if c['locator']:
        page, _, anchor = c['locator'].partition('#')
        claim_link = f'[{c["locator"]}]({rel(page)}#{anchor})' if anchor else f'[{c["locator"]}]({rel(page)})'
    else:  # a claim written through the MCP tools has no page of its own
        claim_link = f'`{c["id"]}` (not on a page)'
    return (f'- **{c["label"]}** · status: {c["status"]} · scope: {c["scope_text"] or "unspecified"} · recorded {c["recorded"] or "unknown"}'
            f' · review by: {c["review_by"] or "not set"} · source: {sources} · claim: {claim_link}')


def feedback_for(graph, claim_id):
    return graph.rows('MATCH (f:Feedback)-[:CORRECTS]->(c:Claim {id: $id}) RETURN f.date, f.label, f.source_locator ORDER BY f.date', {'id': claim_id})


def render_me(graph, p):
    claims = claim_rows(graph)
    lines = header('What the graph knows about me', p)
    for status, title in (('confirmed', 'Confirmed'), ('hypothesis', 'Hypotheses'), ('unknown', 'Unknown'), ('superseded', 'Superseded')):
        group = [c for c in claims if c['status'] == status]
        lines += [f'## {title} ({len(group)})', '']
        lines += [claim_line(c) for c in group] or ['None recorded yet.']
        lines.append('')
    return '\n'.join(lines)


def render_review_queue(graph, p):
    lines = header('Review queue: hypotheses awaiting your confirmation', p)
    group = [c for c in claim_rows(graph) if c['status'] == 'hypothesis']
    if not group:
        lines.append('None recorded yet.')
    for c in group:
        lines.append(claim_line(c))
        for fdate, ftext, floc in feedback_for(graph, c['id']):
            lines.append(f'  - feedback {fdate}: {ftext} ([{floc}]({rel(floc.split(":")[0])}))')
    lines += ['', 'To confirm a hypothesis, add a `> [!feedback]` callout in `memory/feedback.md` linking the claim, then run `python -m duet index`.']
    return '\n'.join(lines) + '\n'


def render_stale(graph, p):
    today = date.today().isoformat()
    lines = header('Stale: claims past their review date', p)
    stale = []
    for c in claim_rows(graph):
        value = (c['review_by'] or '')[:10]
        if len(value) == 10 and value[4] == '-' and value < today:
            stale.append(c)
    lines += [claim_line(c) for c in stale] or ['None recorded yet.']
    return '\n'.join(lines) + '\n'


def render_simple_list(graph, p, title, table, detail):
    """One line per node of a table; detail is the property shown beside the label (status, date)."""
    lines = header(title, p)
    rows = graph.rows(f'MATCH (n:{table}) OPTIONAL MATCH (n)-[:SUPPORTED_BY]->(s:Source) '
                      f'RETURN n.id, n.label, n.{detail}, n.source_locator, collect(s.defined_in) ORDER BY n.id')
    for _, label, value, locator, sources in rows:
        src = ' '.join(f'[{s}]({rel(s)})' for s in sources if s) or 'no source'
        lines.append(f'- **{label}** · {detail}: {value or "n/a"} · source: {src} · locator: {locator}')
    if not rows:
        lines.append('None recorded yet.')
    return '\n'.join(lines) + '\n'


def render_index_report(graph, p):
    lines = header('Last index run', p)
    if not p.index_report.exists():
        return '\n'.join(lines + ['No index run recorded.']) + '\n'
    report = json.loads(p.index_report.read_text())
    lines += [f'- started: {report.get("started")}', f'- finished: {report.get("finished")}',
              f'- parsed: {len(report.get("parsed", []))}', f'- unchanged: {len(report.get("unchanged", []))}',
              f'- rejected: {len(report.get("rejected", []))}', f'- skipped: {len(report.get("skipped", []))}',
              f'- retired: {len(report.get("retired", []))}', f'- broken links: {len(report.get("broken_links", []))}',
              f'- skipped edges: {len(report.get("skipped_edges", []))}', '']
    for item in report.get('rejected', []):
        lines.append(f'- rejected [{item["path"]}]({rel(item["path"])}): {item["message"]}')
    for item in report.get('skipped', []):
        lines.append(f'- skipped {item["path"]}: {item["reason"]}')
    for item in report.get('skipped_edges', []):
        lines.append(f'- skipped edge in [{item["path"]}]({rel(item["path"])}): `{item["edge"]}` ({item["reason"]})')
    for item in report.get('broken_links', []):
        lines.append(f'- broken link in [{item["path"]}]({rel(item["path"])}) line {item["line"]}: `{item["target"]}`')
    return '\n'.join(lines) + '\n'


def project(graph, p):
    p.views.mkdir(parents=True, exist_ok=True)
    outputs = {
        'me.md': render_me(graph, p),
        'review-queue.md': render_review_queue(graph, p),
        'stale.md': render_stale(graph, p),
        'commitments.md': render_simple_list(graph, p, 'Commitments', 'Commitment', 'status'),
        'decisions.md': render_simple_list(graph, p, 'Decisions', 'Decision', 'date'),
        'index-report.md': render_index_report(graph, p),
    }
    for name, text in outputs.items():
        (p.views / name).write_text(text)
    return sorted(outputs)
