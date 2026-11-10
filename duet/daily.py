"""The daily note: where Karteek acts on prepared items, and how those edits return to the graph.

A note is `private/duet/YYYY-MM-DD.md`. Each item is one `## ` section whose header lines carry a
`key:` and a `status:`; the draft follows the first blank line. Only `status:` and the draft are
read back. A section removed from a note that still exists dismisses its item; a note that is
missing changes nothing, because iCloud can evict a file without anyone deleting it.
"""
import re
from collections import defaultdict
from dataclasses import dataclass

from duet.changeset import Changeset, GateError, Node

NOTE_DIR = 'private/duet'
STATUS_RE = re.compile(r'^(surfaced|done|dismissed|snoozed)(?:\s+(\d{4}-\d{2}-\d{2}))?$')
HEADING_RE = re.compile(r'^(#{1,6})(\s.*)$')
FENCES = ('```', '~~~')
HOW_TO = ('Change a `status:` line to `done`, `dismissed` or `snoozed YYYY-MM-DD`, or edit a draft. The next check-in or '
          '`duet index` reads it back. Deleting a section dismisses its item. Nothing else in this note is interpreted.')


@dataclass
class Section:
    title: str
    key: str
    status: str
    snooze_until: str
    draft: str
    problem: str = ''


def note_rel(day):
    return f'{NOTE_DIR}/{day}.md'


def one_line(text):
    return ' '.join(str(text).split())


def normalize_draft(text):
    """Demote headings so the shallowest is level three: a draft can then never open a new section."""
    lines = text.strip().splitlines()
    fenced, levels = False, []
    for line in lines:
        if line.lstrip().startswith(FENCES):
            fenced = not fenced
        elif not fenced and HEADING_RE.match(line):
            levels.append(len(HEADING_RE.match(line).group(1)))
    shift = max(0, 3 - min(levels)) if levels else 0
    out, fenced = [], False
    for line in lines:
        if line.lstrip().startswith(FENCES):
            fenced = not fenced
        elif shift and not fenced and HEADING_RE.match(line):
            hashes, rest = HEADING_RE.match(line).groups()
            line = '#' * min(6, len(hashes) + shift) + rest
        out.append(line.rstrip())
    return '\n'.join(out).strip()


def parse_note(text):
    """Sections that carry a `key:` line, keyed by it; the last section for a key wins."""
    lines = text.splitlines()
    starts, fenced = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(FENCES):
            fenced = not fenced
        elif not fenced and line.startswith('## '):
            starts.append(i)
    found = {}
    for n, start in enumerate(starts):
        body = lines[start + 1:starts[n + 1] if n + 1 < len(starts) else len(lines)]
        header, i = {}, 0
        while i < len(body) and body[i].strip():
            name, sep, value = body[i].partition(':')
            if sep:
                header.setdefault(name.strip().lower(), value.strip())
            i += 1
        if not header.get('key'):
            continue
        raw = header.get('status', '')
        match = STATUS_RE.match(raw.lower())
        status, until, problem = '', '', ''
        if not match:
            problem = f'status "{raw}" is not surfaced, done, dismissed or snoozed YYYY-MM-DD'
        else:
            status, until = match.group(1), match.group(2) or ''
            if status == 'snoozed' and not until:
                problem = 'snoozed needs a date: snoozed YYYY-MM-DD'
            elif status != 'snoozed' and until:
                problem = f'only snoozed takes a date, found "{raw}"'
        found[header['key']] = Section(lines[start][3:].strip(), header['key'], status, until, '\n'.join(body[i:]).strip(), problem)
    return found


def render_section(title, key, why_now, playbook, evidence, draft, rests=(), status='surfaced'):
    lines = [f'## {one_line(title)}', f'key: {key}', f'why now: {one_line(why_now)}', f'status: {status}', f'playbook: [[{playbook}]]']
    if rests:
        lines.append('rests on: ' + ', '.join(rests))
    lines += [f'evidence: {one_line(evidence)}', '', draft, '']
    return '\n'.join(lines)


def append_run(existing, day, now, coverage, sections):
    """The note text after one run: whatever was there, then this run's coverage and new sections."""
    parts = [existing.rstrip('\n'), ''] if existing.strip() else [f'# Duet, {day}', '', HOW_TO, '']
    parts += [f'## Source coverage, run {now.strftime("%H:%M")}', '']
    parts += [f'- {one_line(line)}' for line in coverage] or ['- No coverage was reported.']
    parts.append('')
    if not sections:
        parts += ['No new items in this run.', '']
    for text in sections:
        parts += [text.rstrip('\n'), '']
    return '\n'.join(parts)


def read_back(graph, p):
    """Bring status and draft edits from the daily notes into their DuetItems, as the indexer."""
    out = {'updated': [], 'problems': []}
    by_note = defaultdict(list)
    for row in graph.rows('MATCH (i:DuetItem) RETURN i.id, i.key, i.status, i.snooze_until, i.draft, i.defined_in, i.scope, i.owner ORDER BY i.id'):
        by_note[row[5] or ''].append(row)
    for note, items in sorted(by_note.items()):
        path = p.vault / note
        if not note or not path.is_file():
            continue
        sections = parse_note(path.read_text(errors='replace'))
        nodes = []
        for node_id, key, status, until, draft, _, scope, owner in items:
            section = sections.get(key)
            if section is None:
                if status in ('done', 'dismissed'):
                    continue
                new = ('dismissed', '', draft or '')
            elif section.problem:
                out['problems'].append({'note': note, 'key': key, 'problem': section.problem})
                continue
            else:
                new = (section.status, section.snooze_until, section.draft)
            if new != (status, until or '', draft or ''):
                nodes.append(Node('DuetItem', node_id, {'status': new[0], 'snooze_until': new[1], 'draft': new[2],
                                                         'scope': scope, 'owner': owner}))
        if not nodes:
            continue
        try:
            graph.apply(Changeset(nodes=nodes), 'indexer')
        except GateError as err:
            out['problems'].append({'note': note, 'key': '', 'problem': str(err)})
            continue
        out['updated'] += [node.id for node in nodes]
    return out
