"""Command line for Karteek Duet: index, project, serve, rebuild, status."""
import argparse
import json
import sys

from duet.config import paths


def cmd_status(p):
    print(f'vault: {p.vault}')
    print(f'db: {"present" if p.db.exists() else "absent"} ({p.db})')
    if p.index_report.exists():
        report = json.loads(p.index_report.read_text())
        print(f'last index: {report.get("finished")} parsed={len(report.get("parsed", []))} '
              f'rejected={len(report.get("rejected", []))} skipped={len(report.get("skipped", []))}')
    else:
        print('last index: never')
    return 0


COMMANDS = {'status': cmd_status}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='duet', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Show vault, database and last index run.')
    args = parser.parse_args(argv)
    return COMMANDS[args.command](paths())


if __name__ == '__main__':
    sys.exit(main())
