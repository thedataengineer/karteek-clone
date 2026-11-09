#!/usr/bin/env python3
"""Screen a downloaded Claude Workflows HTML catalog; never execute its content."""
import argparse
from collections import Counter
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import vault_path  # noqa: E402

VAULT = vault_path()
RULES = {
    'meeting-prep': r'meeting prep|meeting preparation|client context',
    'meeting-capture': r'meeting.*(?:transcri|summari|recap|follow)|meeting management',
    'commitment-radar': r'commitment|follow.up|task tracking|task management',
    'account-memory': r'client context|client management|consulting',
    'stakeholder-objections': r'stakeholder|objection',
    'opportunity-strategy': r'sales|business development|market research',
    'prospect-research': r'lead enrichment|lead generation|prospect|b2b',
    'competitive-positioning': r'competitive|competitor|market research',
    'sales-call-coaching': r'sales call|call coaching',
    'discovery-plan': r'product discovery|problem validation|requirements gathering',
    'requirements-analysis': r'requirements|assumption management|prd',
    'architecture-options': r'architectural decision|architecture decision|system design',
    'proposal-workbench': r'proposal',
    'rfp-response': r'\brfp\b|request for proposal',
    'solution-demo': r'proof.of.value|demo|prototyp',
    'executive-storyline': r'presentation|slide|executive|storyline',
    'decision-memo': r'decision.making|critical thinking|thinking partner|strategic planning',
    'delivery-handoff': r'delivery|project handoff|project planning',
    'relationship-follow-through': r'relationship management|contact graph',
    'communication-drafts': r'email draft|communication|professional writing',
    'voice-calibration': r'writing voice|writing style|voice replication|voice preservation',
    'research-brief': r'research|fact.check|citation',
    'knowledge-ingestion': r'knowledge management|knowledge base|second brain|obsidian|ingestion',
    'weekly-review': r'weekly review|gtd|self.reflection',
    'personal-planning': r'life organizer|lifeos|personal planning|personal productivity',
    'travel-preparation': r'travel assistant|travel guide|travel savings|trip research',
    'session-handoff': r'handoff|session continuity|context recovery|checkpoint',
    'memory-maintenance': r'memory|persistent context|context management',
    'quality-review': r'quality control|quality assurance|verification|validation',
    'daily-priorities': r'morning brief|daily brief|proactive|chief.of.staff',
}


class CatalogParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'article' and 'card' in attrs.get('class', '').split():
            self.row = {'title': '', 'search': attrs.get('data-search', ''),
                        'categories': attrs.get('data-categories', ''), 'url': ''}
        if self.row is not None:
            if tag == 'h2':
                self.title = True
            if tag == 'a' and attrs.get('class') == 'discussion':
                self.row['url'] = attrs.get('href', '')

    def handle_data(self, data):
        if self.row is not None and self.title:
            self.row['title'] += data

    def handle_endtag(self, tag):
        if tag == 'h2':
            self.title = False
        if tag == 'article' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def screen(rows, manifest):
    chosen = {}
    for playbook in manifest:
        for source in playbook['source_entries']:
            chosen.setdefault(source['url'], []).append(playbook['id'])
    for row in rows:
        # Use metadata and indexed summaries to screen; not a full-post review.
        text = row['title'] + ' ' + row['search']
        matches = sorted(k for k, pattern in RULES.items() if re.search(pattern, text, re.I))
        adopted = chosen.get(row['url'], [])
        if adopted:
            status = 'adapted-from-reviewed-summary'
            reason = 'Selected catalog summary reviewed; pattern adapted into original Field CTO playbook(s).'
        elif re.search(r'coding|code|debug|programming|game|firmware|kernel|android|ios|react|rust|python|javascript|typescript|npm|ci/cd|git|test|deploy', row['title'], re.I) and not re.search(r'consult|client|sales|meeting|proposal|stakeholder|executive|personal|second brain|writing voice|writing style|life organizer|travel|knowledge|memory|handoff|briefing|decision.making', row['title'], re.I):
            status = 'engineering-reference'
            reason = 'Engineering-oriented title; retained as on-demand technical reference, not default personal-assistant behavior.'
        elif matches:
            status = 'pattern-candidate'
            reason = 'Automated relevance match; may be coding-specific or duplicative. Not individually adopted.'
        else:
            status = 'deferred-no-role-match'
            reason = 'No current role-pattern match in catalog metadata; retained for future scoped lookup.'
        yield {'source_id': row['url'].split('/comments/')[-1].split('/')[0],
               'title': row['title'], 'url': row['url'], 'status': status,
               'playbooks': adopted, 'candidate_patterns': matches, 'reason': reason}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html', type=Path)
    args = parser.parse_args()
    raw = args.html.read_bytes()
    html = raw.decode('utf-8')
    parsed = CatalogParser()
    parsed.feed(html)
    expected = re.search(r'([\d,]+) workflows shown', html)
    if not expected or len(parsed.rows) != int(expected.group(1).replace(',', '')) or '</html>' not in html.lower():
        raise SystemExit('Incomplete catalog download or changed format; refusing to replace inventory.')
    urls = [r['url'] for r in parsed.rows]
    if len(set(urls)) != len(urls) or not all(u.startswith('https://www.reddit.com/r/ClaudeWorkflows/comments/') for u in urls):
        raise SystemExit('Unexpected or duplicate catalog URLs; review before importing.')
    manifest = json.loads((VAULT / 'workflows/manifest.json').read_text())
    output = list(screen(parsed.rows, manifest))
    folder = VAULT / 'workflows/library'
    folder.mkdir(exist_ok=True)
    (folder / 'catalog.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in output))
    summary = {'source': 'https://www.claudeworkflows.org', 'retrieved_on': '2026-11-08',
               'sha256': hashlib.sha256(raw).hexdigest(), 'entries': len(output),
               'counts': dict(Counter(row['status'] for row in output)),
               'playbooks': len(manifest), 'method': 'All catalog entries automatically screened; selected summaries reviewed. Not all linked posts read.'}
    (folder / 'snapshot.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
