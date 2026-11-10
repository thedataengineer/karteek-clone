"""Render link-dense Markdown views of the graph into private/views/."""
import json
import posixpath
from datetime import date

from duet.graph import now_iso
from duet.parse import slugify

VIEWS_DIR = 'private/views'
CHECKIN_CLAIM = 'claim:checkin:'  # claims a check-in recorded: account context and open questions, not statements about Karteek


def rel(target, base=VIEWS_DIR):
    """Vault-relative target as a link from a views folder."""
    return posixpath.relpath(target, base)


def meeting_view(start, label):
    """File name, without extension, of a meeting's view."""
    return f'{(start or "undated")[:10]}-{slugify(label)[:60] or "meeting"}'


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
    about_accounts = {row[0] for row in graph.rows('MATCH (c:Claim)-[:ABOUT]->(:Account) RETURN c.id')}
    claims = [c for c in claim_rows(graph) if c['id'] not in about_accounts and not c['id'].startswith(CHECKIN_CLAIM)]
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


def render_items(graph, p):
    lines = header('Duet items', p)
    rows = graph.rows('MATCH (i:DuetItem) OPTIONAL MATCH (i)-[:USES]->(b:Playbook) '
                      'RETURN i.key, i.label, i.status, i.snooze_until, i.revision, i.why_now, i.defined_in, i.updated, b.label '
                      'ORDER BY i.updated DESC, i.key')
    for status, title in (('surfaced', 'Waiting for you'), ('snoozed', 'Snoozed'), ('done', 'Done'), ('dismissed', 'Dismissed')):
        group = [r for r in rows if r[2] == status]
        lines += [f'## {title} ({len(group)})', '']
        for key, label, _, until, revision, why_now, note, updated, playbook in group:
            where = f'[{note}]({rel(note)})' if note else 'no note'
            snooze = f' until {until}' if until else ''
            lines.append(f'- **{label}** · status: {status}{snooze} · why now: {why_now or "n/a"} · playbook: {playbook or "n/a"}'
                         f' · note: {where} · key: `{key}` · revision: {revision} · updated {updated}')
        lines += [] if group else ['None recorded yet.']
        lines.append('')
    lines.append('To change an item, edit its `status:` line or its draft in the linked daily note, then run `python -m duet index`.')
    return '\n'.join(lines) + '\n'


def render_open_questions(graph, p):
    lines = header('Open questions from check-ins', p)
    rows = graph.rows('MATCH (c:Claim) WHERE c.status = "unknown" AND c.id STARTS WITH $prefix OPTIONAL MATCH (c)-[:ABOUT]->(a:Account) '
                      'RETURN c.label, c.recorded, c.source_locator, a.label ORDER BY c.recorded DESC, c.id', {'prefix': CHECKIN_CLAIM})
    for label, recorded, locator, account in rows:
        lines.append(f'- **{label}** · recorded {recorded or "unknown"} · account: {account or "none stated"} · source: `{locator}`')
    return '\n'.join(lines + ([] if rows else ['None recorded yet.'])) + '\n'


def render_meetings(graph, p):
    """One page per meeting: its source, account, decisions and the items resting on it."""
    pages = {}
    for mid, label, start, locator in graph.rows('MATCH (m:Meeting) RETURN m.id, m.label, m.start, m.source_locator ORDER BY m.start, m.id'):
        lines = header(label, p) + [f'- start: {start or "unknown"}', f'- source: `{locator}`']
        lines += [f'- account: [{a}](../accounts/{slugify(a)}.md)' for (a,) in graph.rows(
            'MATCH (:Meeting {id: $id})-[:CONCERNS]->(a:Account) RETURN a.label ORDER BY a.label', {'id': mid})]
        decisions = graph.rows('MATCH (d:Decision)-[:DECIDED_IN]->(:Meeting {id: $id}) RETURN d.label, d.date ORDER BY d.id', {'id': mid})
        items = graph.rows('MATCH (i:DuetItem)-[:RESTS_ON]->(:Meeting {id: $id}) RETURN i.label, i.status, i.defined_in ORDER BY i.id', {'id': mid})
        lines += ['', f'## Decisions ({len(decisions)})', ''] + [f'- **{d}** · date: {when or "unknown"} · hypothesis from the meeting record'
                                                                  for d, when in decisions]
        lines += ['', f'## Duet items ({len(items)})', '']
        lines += [f'- **{i}** · status: {status} · note: ' + (f'[{note}]({rel(note, VIEWS_DIR + "/meetings")})' if note else 'no note')
                  for i, status, note in items]
        pages[f'meetings/{meeting_view(start, label)}.md'] = '\n'.join(lines) + '\n'
    return pages


def render_accounts(graph, p):
    """One page per account: what check-ins recorded about it. Everything here is private scope."""
    pages = {}
    for aid, label, stage in graph.rows('MATCH (a:Account) RETURN a.id, a.label, a.stage ORDER BY a.id'):
        lines = header(label, p) + [f'- stage: {stage or "unknown"}', '']
        claims = graph.rows('MATCH (c:Claim)-[:ABOUT]->(:Account {id: $id}) RETURN c.label, c.status, c.recorded, c.source_locator ORDER BY c.recorded, c.id', {'id': aid})
        lines += [f'## Context ({len(claims)})', '']
        lines += [f'- **{c}** · status: {status} · recorded {recorded or "unknown"} · source: `{locator}`' for c, status, recorded, locator in claims]
        for table, title, detail in (('Meeting', 'Meetings', 'start'), ('Decision', 'Decisions', 'date'), ('Commitment', 'Commitments', 'status')):
            rows = graph.rows(f'MATCH (n:{table})-[:CONCERNS]->(:Account {{id: $id}}) RETURN n.label, n.{detail}, n.source_locator ORDER BY n.id', {'id': aid})
            lines += ['', f'## {title} ({len(rows)})', '']
            lines += [f'- **{n}** · {detail}: {value or "n/a"} · source: `{locator}`' for n, value, locator in rows]
        pages[f'accounts/{slugify(label)}.md'] = '\n'.join(lines) + '\n'
    return pages


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
    notes = report.get('notes', {})
    for item_id in notes.get('updated', []):
        lines.append(f'- daily note edit applied to `{item_id}`')
    for item in notes.get('problems', []):
        lines.append(f'- daily note [{item["note"]}]({rel(item["note"])}) item `{item["key"]}`: {item["problem"]}')
    return '\n'.join(lines) + '\n'


def project(graph, p):
    p.views.mkdir(parents=True, exist_ok=True)
    outputs = {
        'me.md': render_me(graph, p),
        'review-queue.md': render_review_queue(graph, p),
        'stale.md': render_stale(graph, p),
        'commitments.md': render_simple_list(graph, p, 'Commitments', 'Commitment', 'status'),
        'decisions.md': render_simple_list(graph, p, 'Decisions', 'Decision', 'date'),
        'items.md': render_items(graph, p),
        'open-questions.md': render_open_questions(graph, p),
        'index-report.md': render_index_report(graph, p),
    }
    outputs.update(render_meetings(graph, p))
    outputs.update(render_accounts(graph, p))
    for folder in ('meetings', 'accounts'):  # these folders are regenerated whole: a page for a node that is gone goes too
        (p.views / folder).mkdir(exist_ok=True)
        for stale in (p.views / folder).glob('*.md'):
            if f'{folder}/{stale.name}' not in outputs:
                stale.unlink()
    for name, text in outputs.items():
        (p.views / name).write_text(text)
    return sorted(outputs)
