"""Command line for Karteek Duet: index, project, serve, checkin, schedule, rebuild, status."""
import argparse
import json
import subprocess
import sys

from duet import checkin, daily
from duet.changeset import Changeset, GateError
from duet.config import paths
from duet.ecl import index as run_index
from duet.graph import Graph
from duet.mcp_server import serve as run_serve
from duet.project import project as run_project
from duet.schema import NODE_TABLES


def cmd_status(p, args):
    print(f'vault: {p.vault}')
    print(f'db: {"present" if p.db.exists() else "absent"} ({p.db})')
    if p.index_report.exists():
        report = json.loads(p.index_report.read_text())
        print(f'last index: {report.get("finished")} parsed={len(report.get("parsed", []))} '
              f'rejected={len(report.get("rejected", []))} skipped={len(report.get("skipped", []))}')
    else:
        print('last index: never')
    if p.db.exists():
        with Graph.open(p, read_only=True) as graph:
            counts = graph.counts()
        print('counts: ' + ' '.join(f'{table}={counts[table]}' for table in sorted(NODE_TABLES)))
    return 0


def cmd_index(p, force=False):
    with Graph.open(p) as graph:
        graph.ensure_schema()
        report = run_index(p.vault, graph, force=force)
        run_project(graph, p)  # views are always fresh after an index
    print(f'parsed={len(report["parsed"])} unchanged={len(report["unchanged"])} rejected={len(report["rejected"])} '
          f'skipped={len(report["skipped"])} retired={len(report["retired"])}')
    for item in report['rejected']:
        print(f'  rejected {item["path"]}: {item["message"]}')
    return 1 if report['rejected'] else 0


def cmd_project(p, args):
    with Graph.open(p, read_only=True) as graph:
        names = run_project(graph, p)
    print('views: ' + ', '.join(names))
    return 0


def cmd_rebuild(p, args):
    replay = []
    if p.journal.exists():
        for line in p.journal.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get('actor') != 'indexer' and entry.get('result') == 'applied' and 'nodes' in entry.get('changeset', {}):
                replay.append(entry)
    replayed = failed = 0
    with Graph.open(p, fresh=True) as graph:  # the old database is deleted only once the writer lock is held
        if p.index_state.exists():
            p.index_state.unlink()
        graph.ensure_schema()
        report = run_index(p.vault, graph, force=True)
        for entry in replay:
            try:
                # journaled as 'replayed', so the next rebuild replays the original entry only
                graph.apply(Changeset.from_json(entry['changeset']), entry['actor'], now=entry.get('ts'), result='replayed')
                replayed += 1
            except GateError:
                failed += 1
        daily.read_back(graph, p)  # replayed items start as surfaced; the daily notes hold what Karteek did with them
        run_project(graph, p)
    print(f'rebuilt: parsed={len(report["parsed"])} rejected={len(report["rejected"])} replayed={replayed} replay_failed={failed}')
    return 1 if report['rejected'] or failed else 0


def cmd_serve(p, args):
    run_serve(p, read_only=args.read_only)
    return 0


def cmd_checkin(p, args):
    if args.dry_run:
        print(json.dumps(checkin.dry_run(p, args.notify), indent=2))
        return 0
    try:
        return checkin.run(p, notify=args.notify)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, GateError) as err:
        print(f'Check-in stopped: {err}', file=sys.stderr)
        return 1


def cmd_schedule(p, args):
    if args.remove:
        print(f'Removed {checkin.remove_schedule()}; no check-in is scheduled.')
        return 0
    if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59:
        print('Invalid local schedule time.', file=sys.stderr)
        return 2
    checkin.write_schedule(p, p.schedule_plist, args.hour, args.minute, args.notify)
    when = f'weekdays at {args.hour:02d}:{args.minute:02d}, this Mac\'s local time'
    if not args.install:
        print(f'Generated {p.schedule_plist} for {when}; not installed. Add --install to load it.')
        return 0
    try:
        target = checkin.install_schedule(p.schedule_plist)
    except (RuntimeError, OSError) as err:
        print(f'Schedule not installed: {err}', file=sys.stderr)
        return 1
    print(f'Installed {target}: a check-in runs {when}. Remove it with `python -m duet schedule --remove`.')
    return 0


COMMANDS = {'status': cmd_status, 'index': lambda p, args: cmd_index(p, args.force), 'project': cmd_project, 'serve': cmd_serve,
            'checkin': cmd_checkin, 'schedule': cmd_schedule, 'rebuild': cmd_rebuild}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='duet', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Show vault, database and last index run.')
    idx = sub.add_parser('index', help='Index changed vault pages into the graph.')
    idx.add_argument('--force', action='store_true', help='Re-index every page regardless of hash.')
    sub.add_parser('project', help='Render Markdown views under private/views/.')
    srv = sub.add_parser('serve', help='Run the MCP server over stdio: read tools and gated write tools.')
    srv.add_argument('--read-only', action='store_true', help='Offer the read tools only.')
    chk = sub.add_parser('checkin', help='Run one check-in: Claude prepares work, the result is gated into the graph and the daily note.')
    chk.add_argument('--dry-run', action='store_true', help='Print the Claude command and exit; nothing runs or is written.')
    chk.add_argument('--notify', action='store_true', help='Show a fixed-text macOS notification when something new was saved.')
    sch = sub.add_parser('schedule', help='Write the weekday launchd schedule for check-ins, and optionally install or remove it.')
    sch.add_argument('--hour', type=int, default=7)
    sch.add_argument('--minute', type=int, default=0)
    sch.add_argument('--notify', action='store_true', help='Scheduled check-ins notify when something new was saved.')
    sch.add_argument('--install', action='store_true', help='Copy the schedule into ~/Library/LaunchAgents and load it.')
    sch.add_argument('--remove', action='store_true', help='Unload the schedule and delete the installed file.')
    sub.add_parser('rebuild', help='Delete the graph, re-index the vault, replay non-indexer journal entries, re-render views.')
    args = parser.parse_args(argv)
    return COMMANDS[args.command](paths(), args)


if __name__ == '__main__':
    sys.exit(main())
