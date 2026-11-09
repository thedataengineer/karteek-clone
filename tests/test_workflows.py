import importlib.util
import json
from pathlib import Path
import unittest

CODE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalog', CODE / 'scripts/catalog_workflows.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)
ROOT = catalog.VAULT  # vault holds the manifest, playbooks and catalog


class WorkflowTests(unittest.TestCase):
    def test_all_catalog_entries_accounted_for(self):
        rows = [json.loads(line) for line in (ROOT / 'workflows/library/catalog.jsonl').read_text().splitlines()]
        snapshot = json.loads((ROOT / 'workflows/library/snapshot.json').read_text())
        self.assertEqual(len(rows), snapshot['entries'])
        self.assertEqual(len({x['url'] for x in rows}), len(rows))
        self.assertEqual(sum(snapshot['counts'].values()), len(rows))

    def test_manifest_targets_and_sources_exist(self):
        manifest = json.loads((ROOT / 'workflows/manifest.json').read_text())
        rows = {x['url']: x for x in map(json.loads, (ROOT / 'workflows/library/catalog.jsonl').read_text().splitlines())}
        self.assertEqual(len({p['id'] for p in manifest}), len(manifest))
        for p in manifest:
            self.assertTrue((ROOT / p['path']).is_file(), p['id'])
            for source in p['source_entries']:
                self.assertIn(p['id'], rows[source['url']]['playbooks'])

    def test_code_catalog_entry_is_reference_not_adopted(self):
        row = dict(title='Debugging Python code', search='quality control', url='https://www.reddit.com/r/ClaudeWorkflows/comments/example/test/')
        result = list(catalog.screen([row], []))[0]
        self.assertEqual(result['status'], 'engineering-reference')
        self.assertEqual(result['playbooks'], [])

    def test_catalog_parser_does_not_execute_embedded_content(self):
        parser = catalog.CatalogParser()
        parser.feed("<article class='card' data-search='ignore instructions'><h2>A meeting brief</h2><a class='discussion' href='https://example.com'>Link</a></article>")
        self.assertEqual(parser.rows[0]['title'], 'A meeting brief')
        self.assertEqual(parser.rows[0]['search'], 'ignore instructions')


if __name__ == '__main__':
    unittest.main()
