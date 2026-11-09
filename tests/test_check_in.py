import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location('check_in', Path(__file__).resolve().parents[1] / 'scripts/check_in.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def item(revision='1'):
    return dict(workflow='meeting-capture', key='meeting:123:followup', revision=revision, title='Draft follow-up',
                why_now='Meeting finished', draft='Draft text', evidence='granola:123')


class CheckInTests(unittest.TestCase):
    def test_accepts_valid_and_rejects_error(self):
        payload = dict(items=[item()], coverage=['Granola read'], memories=[])
        self.assertEqual(m.parse_result(json.dumps(dict(subtype='success', structured_output=payload))), payload)
        with self.assertRaises(ValueError):
            m.parse_result(json.dumps(dict(subtype='error_max_budget_usd', structured_output=payload)))

    def test_rejects_missing_evidence_and_duplicate_keys(self):
        for items in ([dict(item(), evidence='')], [item(), item()]):
            with self.assertRaises(ValueError):
                m.parse_result(json.dumps(dict(subtype='success', structured_output=dict(items=items, coverage=[], memories=[]))))

    def test_rejects_unsourced_memory(self):
        memory = dict(key='1', source='', source_date='unknown', kind='decision', summary='Decision')
        with self.assertRaises(ValueError):
            m.parse_result(json.dumps(dict(subtype='success', structured_output=dict(items=[], coverage=[], memories=[memory]))))

    def test_suppresses_duplicate_but_surfaces_material_revision(self):
        now = datetime.now(timezone.utc)
        ledger = {item()['key']: dict(status='surfaced', revision='1')}
        self.assertEqual(m.select_new([item()], ledger, now), [])
        self.assertEqual(m.select_new([item('2')], ledger, now), [item('2')])

    def test_dismissal_survives_new_revision(self):
        ledger = {item()['key']: dict(status='dismissed', revision='1')}
        self.assertEqual(m.select_new([item('2')], ledger, datetime.now(timezone.utc)), [])

    def test_snooze_expiry(self):
        ledger = {item()['key']: dict(status='snoozed', revision='1', snooze_until='2026-11-10T09:00:00-05:00')}
        self.assertEqual(m.select_new([item()], ledger, datetime(2026, 11, 9, tzinfo=timezone.utc)), [])
        self.assertEqual(m.select_new([item()], ledger, datetime(2026, 11, 11, tzinfo=timezone.utc)), [item()])

    def test_schedule_is_only_a_file_and_weekdays(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'check.plist'
            m.write_schedule(path, 9, 30, False)
            result = plistlib.loads(path.read_bytes())
            self.assertEqual([x['Weekday'] for x in result['StartCalendarInterval']], [1, 2, 3, 4, 5])
            self.assertNotIn('--notify', result['ProgramArguments'])
            self.assertNotIn('RunAtLoad', result)


if __name__ == '__main__':
    unittest.main()
