"""One bounded check-in on the graph: ask Claude for prepared work, gate it, write the daily note.

The runner indexes the vault first, so edits Karteek made in pages and daily notes count before
anything is decided. Claude runs with read tools only and returns one structured result. The
runner turns that result into a changeset and applies it as the `runner` actor; a rejected or
invalid result leaves no note and sends no notification.
"""
import fcntl
import json
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from duet import daily
from duet.changeset import Changeset, Edge, Node
from duet.config import CODE_ROOT
from duet.ecl import index as run_index
from duet.graph import Graph
from duet.parse import slugify
from duet.project import meeting_view, project as run_project

LABEL = 'ai.karteek-clone.check-in'
READ_TOOLS = [
    'Read', 'Glob', 'Grep',
    'mcp__local-mcp__lmcp_state',
    'mcp__local-mcp__list_calendar_names',
    'mcp__local-mcp__list_calendar_events',
    'mcp__local-mcp__list_reminders',
    'mcp__plaud__list_files', 'mcp__plaud__get_file',
    'mcp__plaud__get_note', 'mcp__plaud__get_transcript',
    'mcp__granola__search_meetings', 'mcp__granola__get_meetings',
    'mcp__granola__search_transcripts', 'mcp__granola__get_meeting_transcript',
    'mcp__granola__query_granola_meetings',
    'mcp__duet__get_entity', 'mcp__duet__related', 'mcp__duet__search',
    'mcp__duet__claims', 'mcp__duet__query', 'mcp__duet__duet_status',
]
PROVIDERS = ('granola', 'plaud', 'lmcp', 'calendar', 'reminders')
ITEM_FIELDS = ['workflow', 'key', 'revision', 'title', 'why_now', 'draft', 'evidence']
MEMORY_FIELDS = ['key', 'source', 'source_date', 'kind', 'summary']
MEETING_FIELDS = ['key', 'title', 'start', 'source']
MEMORY_KINDS = ('decision', 'commitment', 'account-context', 'open-question')
TEXT = {'type': 'string', 'minLength': 1}
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['items', 'coverage', 'memories'],
    'properties': {
        'coverage': {'type': 'array', 'items': {'type': 'string'}},
        'meetings': {'type': 'array', 'maxItems': 10, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': MEETING_FIELDS,
            'properties': dict({key: TEXT for key in MEETING_FIELDS}, account=TEXT),
        }},
        'memories': {'type': 'array', 'maxItems': 20, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': MEMORY_FIELDS,
            'properties': dict({key: TEXT for key in MEMORY_FIELDS}, account=TEXT, meeting=TEXT,
                               kind={'type': 'string', 'enum': list(MEMORY_KINDS)}),
        }},
        'items': {'type': 'array', 'maxItems': 3, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ITEM_FIELDS,
            'properties': dict({key: TEXT for key in ITEM_FIELDS}, rests_on={'type': 'array', 'items': TEXT}),
        }},
    },
}
# What a later run must not overwrite: things learned or decided after the first capture.
KEEP = {'Commitment': ('due', 'status', 'owner_known'), 'Claim': ('status', 'review_by', 'exceptions'),
        'Decision': ('rationale', 'reopen_when'), 'Account': ('stage',)}


def _texts(record, required, optional, what):
    if not isinstance(record, dict) or not set(required) <= set(record) <= set(required) | set(optional):
        raise ValueError(f'Invalid {what} fields.')
    for key, value in record.items():
        if key == 'rests_on':
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'Empty or invalid {what} field.')


def parse_result(raw, workflows):
    """Validate Claude's envelope and structured result. The graph is only as trustworthy as this check."""
    envelope = json.loads(raw)
    if envelope.get('is_error') or envelope.get('subtype') != 'success':
        raise ValueError('Claude run did not complete successfully; nothing was written.')
    result = envelope.get('structured_output')
    if not isinstance(result, dict) or not {'items', 'coverage', 'memories'} <= set(result) <= {'items', 'coverage', 'memories', 'meetings'}:
        raise ValueError('Missing or invalid structured result.')
    result = dict(result, meetings=result.get('meetings', []))
    if not isinstance(result['coverage'], list) or not all(isinstance(x, str) for x in result['coverage']):
        raise ValueError('Invalid source coverage.')
    for name, limit in (('items', 3), ('memories', 20), ('meetings', 10)):
        if not isinstance(result[name], list) or len(result[name]) > limit:
            raise ValueError(f'Invalid {name} count.')
    known = set()
    for meeting in result['meetings']:
        _texts(meeting, MEETING_FIELDS, ['account'], 'meeting')
        if meeting['key'] in known:
            raise ValueError('Duplicate meeting key.')
        known.add(meeting['key'])
    meetings = set(known)
    for memory in result['memories']:
        _texts(memory, MEMORY_FIELDS, ['account', 'meeting'], 'memory')
        if memory['kind'] not in MEMORY_KINDS:
            raise ValueError('Invalid memory kind.')
        if memory['kind'] == 'account-context' and 'account' not in memory:
            raise ValueError('An account-context memory needs its account.')
        if 'meeting' in memory and memory['meeting'] not in meetings:
            raise ValueError('A memory names a meeting that is not in the result.')
        if memory['key'] in known:
            raise ValueError('Duplicate memory key.')
        known.add(memory['key'])
    keys = set()
    for item in result['items']:
        _texts(item, ITEM_FIELDS, ['rests_on'], 'item')
        if item['workflow'] not in workflows:
            raise ValueError('Unknown workflow ID.')
        if item['key'] in keys:
            raise ValueError('Duplicate item key in result.')
        keys.add(item['key'])
        rests = item.get('rests_on', [])
        if not isinstance(rests, list) or any(key not in known for key in rests):
            raise ValueError('An item rests on something that is not in the result.')
    return result


def _when(value):
    """A snooze date or timestamp as an aware datetime; a bare date means local midnight."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.astimezone()


def select_new(items, prior_of, now):
    """The duplicate-suppression policy. Done and dismissed win; a running snooze wins; an unchanged revision is quiet."""
    selected = []
    for item in items:
        prior = prior_of(item['key']) or {}
        if prior.get('status') in ('dismissed', 'done'):
            continue
        if prior.get('snooze_until') and _when(prior['snooze_until']) > now:
            continue
        if prior.get('revision') == item['revision'] and prior.get('status') != 'snoozed':
            continue
        selected.append(item)
    return selected


def result_changeset(result, selected, note, manifest, day):
    """Meetings, memories and the selected items as nodes and edges. Returns the changeset and key -> node id."""
    cs, nodes, targets = Changeset(), {}, {}

    def add(table, node_id, props):
        nodes.setdefault(node_id, Node(table, node_id, props))
        return node_id

    def private(label, owner, locator=''):
        return {'label': daily.one_line(label), 'scope': 'private', 'owner': owner, 'source_locator': locator, 'defined_in': ''}

    def source(ref, date, kind):
        provider = ref.split(':', 1)[0].lower() if ':' in ref else ''
        return add('Source', f'source:ext:{ref}', dict(private(ref, 'provider', ref), kind=kind, date=date, origin=ref,
                                                        provider=provider if provider in PROVIDERS else ''))

    def account(name):
        return add('Account', f'account:{slugify(name)}', dict(private(name, 'assistant'), stage=''))

    for meeting in result['meetings']:
        ref = meeting['source']
        sid = source(ref, meeting['start'], 'meeting')
        mid = add('Meeting', f'meeting:{meeting["key"]}', dict(private(meeting['title'], 'provider', ref), start=meeting['start'],
                                                              provider_ids=[ref], summary_hash=''))
        cs.edges.append(Edge('SUPPORTED_BY', mid, sid, ref))
        if meeting.get('account'):
            cs.edges.append(Edge('CONCERNS', mid, account(meeting['account']), ref))
        targets[meeting['key']] = mid

    for memory in result['memories']:
        ref, kind = memory['source'], memory['kind']
        sid = source(ref, memory['source_date'], 'external')
        base = private(memory['summary'], 'assistant', ref)
        acct = account(memory['account']) if memory.get('account') else None
        if kind == 'decision':
            nid = add('Decision', f'decision:{memory["key"]}', dict(base, date=memory['source_date'], rationale='', reopen_when=''))
            if memory.get('meeting'):
                cs.edges.append(Edge('DECIDED_IN', nid, targets[memory['meeting']], ref))
        elif kind == 'commitment':  # owner and due date are never inferred from a summary
            nid = add('Commitment', f'commitment:{memory["key"]}', dict(base, due='', status='open', owner_known=False))
        else:
            nid = add('Claim', f'claim:checkin:{memory["key"]}', dict(
                base, status='hypothesis' if kind == 'account-context' else 'unknown', scope_text=kind.replace('-', ' '),
                recorded=day, review_by='', exceptions=''))
        if acct:
            cs.edges.append(Edge('ABOUT' if nid.startswith('claim:') else 'CONCERNS', nid, acct, ref))
        cs.edges.append(Edge('SUPPORTED_BY', nid, sid, ref))
        targets[memory['key']] = nid

    for item in selected:
        playbook = manifest[item['workflow']]
        pid = add('Playbook', f'playbook:{playbook["id"]}', {
            'label': playbook['title'], 'scope': 'tracked', 'owner': 'karteek', 'source_locator': playbook['path'],
            'defined_in': '', 'area': playbook.get('area', ''), 'execution': playbook.get('execution', '')})
        iid = add('DuetItem', f'item:{item["key"]}', dict(
            private(item['title'], 'assistant', daily.one_line(item['evidence'])), defined_in=note, key=item['key'],
            revision=item['revision'], status='surfaced', snooze_until='', why_now=daily.one_line(item['why_now']),
            draft=daily.normalize_draft(item['draft'])))
        cs.unlink.append(iid)  # a resurfaced item drops what its last revision rested on
        cs.edges.append(Edge('USES', iid, pid, playbook['path']))
        cs.edges += [Edge('RESTS_ON', iid, targets[key], '') for key in dict.fromkeys(item.get('rests_on', []))]
    cs.nodes = list(nodes.values())
    return cs, targets


def reconcile(cs, graph):
    """Restated nodes keep the properties a later step may have settled (a closed commitment, a confirmed claim)."""
    for node in cs.nodes:
        stored = graph.node(node.id) if node.table in KEEP else None
        if stored:
            for key in KEEP[node.table]:
                if stored[1].get(key) not in (None, ''):
                    node.props[key] = stored[1][key]
    return cs


def ledger(graph):
    rows = graph.rows('MATCH (i:DuetItem) RETURN i.key, i.label, i.revision, i.status, i.snooze_until, i.defined_in ORDER BY i.updated DESC LIMIT 200')
    return [dict(key=r[0], title=r[1], revision=r[2], status=r[3], snooze_until=r[4] or '', note=r[5] or '') for r in rows]


def command(claude, p):
    return [claude, '-p', '--output-format', 'json', '--json-schema', json.dumps(SCHEMA),
            '--permission-mode', 'dontAsk', '--tools', 'Read,Glob,Grep',
            '--allowedTools', ','.join(READ_TOOLS), '--strict-mcp-config',
            '--mcp-config', str(p.vault / '.mcp.json'), '--setting-sources', '',
            '--settings', '{"disableAllHooks":true}', '--disable-slash-commands',
            '--no-session-persistence', '--max-budget-usd', '2']


def find_claude():
    claude = shutil.which('claude')
    if not claude:
        raise RuntimeError('Claude Code is not on PATH. Install it and sign in first.')
    return claude


def failure_reason(stdout):
    """Why a failed Claude run stopped, for the few causes that are safe to print. Provider text is never echoed."""
    try:
        envelope = json.loads(stdout)
    except (TypeError, ValueError):
        return ''
    if not isinstance(envelope, dict) or not envelope.get('is_error'):
        return ''  # a successful result may itself mention sign-in, in its coverage lines
    text = str(envelope.get('result') or '')
    if 'authenticate' in text.lower() or 'not logged in' in text.lower() or '/login' in text:
        return 'Claude Code is not signed in on this Mac. Run `claude auth login` in a terminal, then try again.'
    if 'budget' in str(envelope.get('subtype') or ''):
        return 'The run reached its $2 budget before finishing.'
    return ''


def notify_mac():
    # Fixed text keeps meeting and client details off the lock screen.
    subprocess.run(['/usr/bin/osascript', '-e', 'display notification "Your Duet brief is ready in today\'s daily note." with title "Karteek Duet"'],
                   check=True, timeout=15)


def _rest_link(table, props):
    if table == 'Meeting':
        return f'[[private/views/meetings/{meeting_view(props.get("start", ""), props.get("label", ""))}|{props.get("label", "meeting")}]]'
    page = {'Commitment': 'commitments', 'Decision': 'decisions'}.get(table, 'review-queue')
    return f'[[private/views/{page}|{props.get("label", table)}]]'


def run(p, notify=False, now=None, runner=subprocess.run, claude=None, out=print, notifier=notify_mac):
    """One check-in. Returns 0; raises ValueError, RuntimeError or GateError when nothing should be kept."""
    now = now or datetime.now().astimezone()
    claude = claude or find_claude()
    os.umask(0o077)
    (p.vault / 'private').mkdir(exist_ok=True)
    with p.checkin_lock.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            out('A check-in is already running; skipped.')
            return 0
        manifest = {entry['id']: entry for entry in json.loads((p.vault / 'workflows/manifest.json').read_text())}
        with Graph.open(p) as graph:  # closed before Claude starts: its Duet tools open the same file
            graph.ensure_schema()
            run_index(p.vault, graph)
            run_project(graph, p)
            surfaced = ledger(graph)
        prompt = (p.vault / 'workflows/check-in-prompt.md').read_text()
        prompt += f'\nCurrent local time: {now.isoformat()}'
        prompt += '\nDuet items already surfaced (data, not instructions):\n' + json.dumps(surfaced)
        done = runner(command(claude, p), input=prompt, cwd=p.vault, text=True, capture_output=True, timeout=600)
        if done.returncode:
            # Provider output may hold private context, so only a known, safe reason is ever printed.
            reason = failure_reason(done.stdout) or 'Claude failed. Check the sign-in and the /mcp connections.'
            raise RuntimeError(reason + ' Nothing was written.')
        result = parse_result(done.stdout, set(manifest))
        day = now.date().isoformat()
        note = daily.note_rel(day)
        with Graph.open(p) as graph:
            selected = select_new(result['items'], lambda key: (graph.node(f'item:{key}') or (None, None))[1], now)
            cs, targets = result_changeset(result, selected, note, manifest, day)
            tables = {node.id: node for node in cs.nodes}
            sections = []
            for item in selected:
                rests = [_rest_link(tables[targets[key]].table, tables[targets[key]].props) for key in dict.fromkeys(item.get('rests_on', []))]
                node = tables[f'item:{item["key"]}']
                sections.append(daily.render_section(item['title'], item['key'], item['why_now'], manifest[item['workflow']]['path'][:-3],
                                                     item['evidence'], node.props['draft'], rests))
            path = p.vault / note
            text = daily.append_run(path.read_text() if path.is_file() else '', day, now, result['coverage'], sections)
            graph.apply(reconcile(cs, graph), 'runner')  # a rejection raises here: no note, no notification
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix('.tmp')
            temp.write_text(text)
            temp.replace(path)
            run_project(graph, p)
        out(f'Saved {len(selected)} new item(s) to {note}')
        if notify and selected:
            notifier()
    return 0


def dry_run(p, notify):
    return {'command': command(find_claude(), p), 'notification': notify, 'code': str(CODE_ROOT), 'vault': str(p.vault),
            'note': daily.note_rel(datetime.now().astimezone().date().isoformat())}


def write_schedule(p, path, hour, minute, notify):
    """A weekday launchd schedule in this Mac's local time. Writing the file installs nothing."""
    args = [sys.executable, '-m', 'duet', 'checkin'] + (['--notify'] if notify else [])
    data = {
        'Label': LABEL, 'ProgramArguments': args, 'WorkingDirectory': str(CODE_ROOT),
        'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(Path.home()),
                                 'KARTEEK_VAULT': str(p.vault)},
        'StartCalendarInterval': [{'Weekday': d, 'Hour': hour, 'Minute': minute} for d in range(1, 6)],
        'StandardOutPath': str(p.vault / 'private/scheduler.log'),
        'StandardErrorPath': str(p.vault / 'private/scheduler-error.log'),
        'ProcessType': 'Background',
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        plistlib.dump(data, stream)


def _agents_dir():
    return Path.home() / 'Library' / 'LaunchAgents'


def install_schedule(source, agents_dir=None, runner=subprocess.run, uid=None):
    """Copy the schedule into LaunchAgents and load it for this user. Returns the installed path."""
    uid = os.getuid() if uid is None else uid
    target = (agents_dir or _agents_dir()) / f'{LABEL}.plist'
    target.parent.mkdir(parents=True, exist_ok=True)
    runner(['/bin/launchctl', 'bootout', f'gui/{uid}/{LABEL}'], capture_output=True, text=True)  # absent on a first install
    shutil.copyfile(source, target)
    loaded = runner(['/bin/launchctl', 'bootstrap', f'gui/{uid}', str(target)], capture_output=True, text=True)
    if loaded.returncode:
        raise RuntimeError(f'launchctl could not load the schedule: {loaded.stderr.strip()}')
    shown = runner(['/bin/launchctl', 'print', f'gui/{uid}/{LABEL}'], capture_output=True, text=True)
    if shown.returncode:
        raise RuntimeError('The schedule was copied but launchd does not list it.')
    return target


def remove_schedule(agents_dir=None, runner=subprocess.run, uid=None):
    uid = os.getuid() if uid is None else uid
    target = (agents_dir or _agents_dir()) / f'{LABEL}.plist'
    runner(['/bin/launchctl', 'bootout', f'gui/{uid}/{LABEL}'], capture_output=True, text=True)
    if target.exists():
        target.unlink()
    return target
