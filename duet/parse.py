"""Read one vault page into plain data: frontmatter, title, links, claim blocks, feedback callouts."""
import hashlib
import posixpath
import re
from dataclasses import dataclass, field

LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)\s]+)\)')
FIELD_RE = re.compile(r'^- \*\*(.+?):\*\*\s*(.*)$')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*$')
CALLOUT_RE = re.compile(r'^>\s*\[!feedback\]\s*(.*)$', re.I)


@dataclass
class Link:
    text: str
    target: str | None
    fragment: str | None
    line: int


@dataclass
class ClaimBlock:
    heading: str
    slug: str
    fields: dict
    evidence_links: list
    supersedes_links: list
    line: int


@dataclass
class FeedbackCallout:
    date: str
    claim_target: tuple | None
    scope: str
    applied: bool
    text: str
    line: int


@dataclass
class Page:
    path: str
    hash: str
    frontmatter: dict
    title: str
    links: list = field(default_factory=list)
    claims: list = field(default_factory=list)
    feedback: list = field(default_factory=list)


def slugify(text):
    text = re.sub(r"['’]", '', text.lower())
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    return re.sub(r'\s', '-', text.strip())


def resolve_link(page_path, target):
    if re.match(r'^[a-z][a-z0-9+.-]*:', target) or target.startswith('#'):
        return None, None
    path, _, fragment = target.partition('#')
    if not path.endswith('.md'):
        return None, None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), path))
    if resolved.startswith('..'):
        return None, None
    return resolved, fragment or None


def _parse_frontmatter(lines):
    if not lines or lines[0].strip() != '---':
        return {}, 0
    data = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == '---':
            return data, i + 1
        if ':' not in line:
            continue
        key, _, value = line.partition(':')
        value = value.strip()
        if value.startswith('[') and value.endswith(']'):
            data[key.strip()] = [v.strip().strip('"').strip("'") for v in value[1:-1].split(',') if v.strip()]
        else:
            data[key.strip()] = value.strip('"').strip("'")
    return {}, 0


def _links_in(text, page_path, line_no):
    out = []
    for m in LINK_RE.finditer(text):
        target, fragment = resolve_link(page_path, m.group(2))
        out.append(Link(m.group(1), target, fragment, line_no))
    return out


def parse_page(vault, rel_path):
    raw = (vault / rel_path).read_bytes()
    lines = raw.decode('utf-8', errors='replace').splitlines()
    frontmatter, start = _parse_frontmatter(lines)
    page = Page(path=rel_path, hash=hashlib.sha256(raw).hexdigest(), frontmatter=frontmatter, title=rel_path)

    sections = []  # (heading, line_no, [lines])
    current = None
    i = start
    while i < len(lines):
        line = lines[i]
        heading = HEADING_RE.match(line)
        if heading:
            if len(heading.group(1)) == 1 and page.title == rel_path:
                page.title = heading.group(2)
            current = (heading.group(2), i + 1, [])
            sections.append(current)
        elif CALLOUT_RE.match(line):
            page.feedback.append(_parse_callout(lines, i, rel_path))
        elif current is not None:
            current[2].append(line)
        page.links.extend(_links_in(line, rel_path, i + 1))
        i += 1

    for heading, line_no, body in sections:
        fields, evidence, supersedes = {}, [], []
        for offset, line in enumerate(body):
            m = FIELD_RE.match(line)
            if not m:
                continue
            key = m.group(1).strip().lower()
            fields[key] = m.group(2).strip()
            if key == 'evidence':
                evidence = [l for l in _links_in(m.group(2), rel_path, line_no + offset + 1) if l.target]
            if key == 'supersedes':
                supersedes = [l for l in _links_in(m.group(2), rel_path, line_no + offset + 1) if l.target]
        if 'claim' in fields:
            page.claims.append(ClaimBlock(heading, slugify(heading), fields, evidence, supersedes, line_no))
    return page


def _parse_callout(lines, start, rel_path):
    date = CALLOUT_RE.match(lines[start]).group(1).strip()
    claim_target, scope, applied, text = None, '', False, []
    for line in lines[start + 1:]:
        if not line.startswith('>'):
            break
        content = line[1:].strip()
        key, sep, value = content.partition(':')
        key = key.strip().lower()
        if sep and key == 'claim':
            links = [l for l in _links_in(value, rel_path, 0) if l.target]
            claim_target = (links[0].target, links[0].fragment) if links else None
        elif sep and key == 'scope':
            scope = value.strip()
        elif sep and key == 'applied':
            applied = value.strip().lower() in ('yes', 'true')
        else:
            text.append(content)
    return FeedbackCallout(date, claim_target, scope, applied, ' '.join(text).strip(), start + 1)
