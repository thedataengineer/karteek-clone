import json
import shutil
import tempfile
import unittest
from pathlib import Path

from duet.config import paths
from duet.ecl import index, iter_pages
from duet.graph import Graph

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'


def copy_fixture():
    folder = Path(tempfile.mkdtemp()) / 'vault'
    shutil.copytree(FIXTURE, folder)
    (folder / '.obsidian').mkdir()
    (folder / '.obsidian' / 'ignored.md').write_text('# no\n')
    (folder / 'private' / 'views').mkdir(parents=True)
    (folder / 'private' / 'views' / 'me.md').write_text('# generated\n')
    return folder


class IndexerTests(unittest.TestCase):
    def setUp(self):
        self.vault = copy_fixture()
        self.p = paths(vault=self.vault)
        self.g = Graph.open(self.p)
        self.g.ensure_schema()

    def tearDown(self):
        self.g.close()

    def test_eligible_pages(self):
        pages = iter_pages(self.vault)
        self.assertIn('identity/profile.md', pages)
        self.assertIn('private/accounts/acme/context.md', pages)
        self.assertNotIn('.obsidian/ignored.md', pages)
        self.assertNotIn('private/views/me.md', pages)

    def test_full_index_of_fixture(self):
        report = index(self.vault, self.g)
        self.assertEqual(report['rejected'], [])
        self.assertEqual(len(report['parsed']), 6)
        counts = self.g.counts()
        self.assertEqual((counts['Document'], counts['Source'], counts['Claim'], counts['Feedback'], counts['Concept'], counts['Person']),
                         (6, 1, 2, 1, 1, 2))
        self.assertEqual(self.g.node('source:2026-11-08-role')[1]['kind'], 'statement')
        self.assertEqual(self.g.node('claim:identity/profile:professional-role')[1]['status'], 'confirmed')
        self.assertEqual(self.g.neighbors('feedback:memory/feedback:1', 'CORRECTS', 'out')[0][2], 'claim:identity/profile:likes-short-emails')
        self.assertEqual(self.g.node('doc:private/accounts/acme/context.md')[1]['scope'], 'private')
        self.assertEqual([t[2] for t in self.g.neighbors('doc:private/accounts/acme/context.md', 'MENTIONS', 'out')],
                         ['claim:identity/profile:professional-role'])
        self.assertEqual([t[2] for t in self.g.neighbors('concept:second-brain', 'ATTRIBUTED_TO', 'out')], ['person:jane-author'])
        self.assertTrue(self.g.neighbors('doc:wiki/second-brain.md', 'LINKS_TO', 'out'))
        self.assertTrue(self.p.index_report.exists())

    def test_incremental_and_force(self):
        index(self.vault, self.g)
        second = index(self.vault, self.g)
        self.assertEqual(len(second['unchanged']), 6)
        self.assertEqual(second['parsed'], [])
        third = index(self.vault, self.g, force=True)
        self.assertEqual(len(third['parsed']), 6)

    def test_rejected_page_does_not_block_others(self):
        (self.vault / 'identity' / 'bad.md').write_text(
            '# Bad\n\n## Unsourced\n\n- **Claim:** Something.\n- **Status:** confirmed\n- **Evidence:** [gone](../sources/missing.md)\n')
        report = index(self.vault, self.g)
        self.assertEqual([r['path'] for r in report['rejected']], ['identity/bad.md'])
        self.assertIn(report['rejected'][0]['gate'], ('integrity', 'evidence'))
        self.assertEqual(self.g.counts()['Claim'], 2)

    def test_retire_on_edit_and_delete(self):
        index(self.vault, self.g)
        profile = self.vault / 'identity' / 'profile.md'
        text = profile.read_text()
        profile.write_text(text[:text.index('## Likes short emails')])
        (self.vault / 'wiki' / 'second-brain.md').unlink()
        report = index(self.vault, self.g)
        self.assertIsNone(self.g.node('claim:identity/profile:likes-short-emails'))
        self.assertIsNone(self.g.node('doc:wiki/second-brain.md'))
        self.assertIsNone(self.g.node('concept:second-brain'))
        self.assertIn('claim:identity/profile:likes-short-emails', report['retired'])
        self.assertIn('doc:wiki/second-brain.md', report['retired'])

    def test_templates_are_documents_only(self):
        (self.vault / 'templates').mkdir()
        (self.vault / 'templates' / 'claim.md').write_text(
            '---\ntype: knowledge\nauthor: "Someone"\n---\n\n# Claim template\n\n## Heading\n\n'
            '- **Claim:** One falsifiable statement.\n- **Status:** confirmed\n- **Evidence:** path to a source page\n')
        report = index(self.vault, self.g)
        self.assertEqual(report['rejected'], [])
        counts = self.g.counts()
        self.assertEqual((counts['Document'], counts['Claim'], counts['Concept'], counts['Person']), (7, 2, 1, 2))

    def test_edit_drops_stale_edges(self):
        index(self.vault, self.g)
        second = self.vault / 'sources' / '2026-11-09-second.md'
        second.write_text('---\ntype: personal-source\ndate: 2026-11-09\n---\n\n# Second statement\n\n> Short emails, yes.\n')
        profile = self.vault / 'identity' / 'profile.md'
        profile.write_text(profile.read_text().replace(
            '[Role statement](../sources/2026-11-08-role.md#derived-claims)', '[Second statement](../sources/2026-11-09-second.md)'))
        wiki = self.vault / 'wiki' / 'second-brain.md'
        wiki.write_text(wiki.read_text().replace('See the [profile](../identity/profile.md) for how this vault applies it.', 'No links now.'))
        report = index(self.vault, self.g)
        self.assertEqual(report['rejected'], [])
        self.assertEqual([t[2] for t in self.g.neighbors('claim:identity/profile:likes-short-emails', 'SUPPORTED_BY', 'out')],
                         ['source:2026-11-09-second'])
        self.assertEqual(self.g.neighbors('doc:wiki/second-brain.md', 'LINKS_TO', 'out'), [])
        # the unchanged feedback page still corrects the claim, and the unchanged Acme page still mentions the other one
        self.assertEqual(len(self.g.neighbors('claim:identity/profile:likes-short-emails', 'CORRECTS', 'in')), 1)
        self.assertEqual(len(self.g.neighbors('claim:identity/profile:professional-role', 'MENTIONS', 'in')), 2)
        self.assertEqual([json.loads(h) for h in self.g.node('claim:identity/profile:likes-short-emails')[1]['history']], [])

    def test_unchanged_page_is_relinked_when_its_target_returns(self):
        index(self.vault, self.g)
        profile = self.vault / 'identity' / 'profile.md'
        original = profile.read_text()
        profile.write_text(original[:original.index('## Likes short emails')])
        index(self.vault, self.g)
        self.assertEqual(self.g.neighbors('feedback:memory/feedback:1', 'CORRECTS', 'out'), [])
        profile.write_text(original)
        report = index(self.vault, self.g)
        self.assertEqual(report['parsed'], ['identity/profile.md'])
        self.assertEqual(self.g.neighbors('feedback:memory/feedback:1', 'CORRECTS', 'out')[0][2], 'claim:identity/profile:likes-short-emails')

    def test_shared_person_survives_page_deletion(self):
        (self.vault / 'wiki' / 'other.md').write_text('---\ntype: knowledge\nauthor: "Jane Author"\n---\n\n# Other idea\n')
        index(self.vault, self.g)
        (self.vault / 'wiki' / 'second-brain.md').unlink()
        index(self.vault, self.g)
        self.assertEqual([t[2] for t in self.g.neighbors('concept:other', 'ATTRIBUTED_TO', 'out')], ['person:jane-author'])

    def test_broken_links_are_reported_on_every_run(self):
        (self.vault / 'wiki' / 'dangling.md').write_text('# Dangling\n\nSee [gone](missing.md).\n')
        expected = [{'path': 'wiki/dangling.md', 'line': 3, 'target': 'wiki/missing.md'}]
        self.assertEqual(index(self.vault, self.g)['broken_links'], expected)
        second = index(self.vault, self.g)
        self.assertEqual(second['parsed'], [])
        self.assertEqual(second['broken_links'], expected)


if __name__ == '__main__':
    unittest.main()
