"""Command line for Karteek Duet: index, project, serve, rebuild, status."""
import argparse
import json
import sys

from duet.config import paths
from duet.ecl import index as run_index
from duet.graph import Graph
from duet.mcp_server import serve as run_serve
from duet.project import project as run_project


def cmd_status(p, args):
    print(f'vault: {p.vault}')
    print(f'db: {"present" if p.db.exists() else "absent"} ({p.db})')
    if p.index_report.exists():
        report = json.loads(p.index_report.read_text())
        print(f'last index: {report.get("finished")} parsed={len(report.get("parsed", []))} '
              f'rejected={len(report.get("rejected", []))} skipped={len(report.get("skipped", []))}')
    else:
        print('last index: never')
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


def cmd_serve(p, args):
    run_serve(p)
    return 0


COMMANDS = {'status': cmd_status, 'index': lambda p, args: cmd_index(p, args.force), 'project': cmd_project, 'serve': cmd_serve}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='duet', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Show vault, database and last index run.')
    idx = sub.add_parser('index', help='Index changed vault pages into the graph.')
    idx.add_argument('--force', action='store_true', help='Re-index every page regardless of hash.')
    sub.add_parser('project', help='Render Markdown views under private/views/.')
    sub.add_parser('serve', help='Run the read-only MCP server over stdio.')
    args = parser.parse_args(argv)
    return COMMANDS[args.command](paths(), args)


if __name__ == '__main__':
    sys.exit(main())
