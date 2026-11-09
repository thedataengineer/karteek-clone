import hashlib
import unittest
from pathlib import Path

from duet.parse import parse_page, resolve_link, slugify

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'vault'


class ParseTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify('Professional role'), 'professional-role')
        self.assertEqual(slugify('Exceptions / conflicts'), 'exceptions--conflicts')
        self.assertEqual(slugify("Karteek's voice!"), 'karteeks-voice')

    def test_resolve_link(self):
        self.assertEqual(resolve_link('identity/profile.md', '../sources/x.md#frag'), ('sources/x.md', 'frag'))
        self.assertEqual(resolve_link('identity/profile.md', 'onboarding.md'), ('identity/onboarding.md', None))
        self.assertEqual(resolve_link('a.md', 'https://example.com/x.md'), (None, None))
        self.assertEqual(resolve_link('a.md', 'image.png'), (None, None))
        self.assertEqual(resolve_link('a.md', '#local'), (None, None))

    def test_frontmatter_and_hash(self):
        page = parse_page(FIXTURE, 'wiki/second-brain.md')
        self.assertEqual(page.frontmatter['type'], 'knowledge')
        self.assertEqual(page.frontmatter['author'], 'Jane Author')
        self.assertEqual(page.frontmatter['tags'], ['concept', 'knowledge-management'])
        self.assertEqual(page.title, 'Second brain')
        self.assertEqual(page.hash, hashlib.sha256((FIXTURE / 'wiki/second-brain.md').read_bytes()).hexdigest())
        self.assertEqual(parse_page(FIXTURE, 'identity/profile.md').frontmatter, {})

    def test_links(self):
        page = parse_page(FIXTURE, 'wiki/second-brain.md')
        self.assertEqual([(l.target, l.fragment) for l in page.links], [('identity/profile.md', None)])
        private = parse_page(FIXTURE, 'private/accounts/acme/context.md')
        self.assertEqual([(l.target, l.fragment) for l in private.links], [('identity/profile.md', 'professional-role')])

    def test_claim_blocks(self):
        page = parse_page(FIXTURE, 'identity/profile.md')
        self.assertEqual([c.slug for c in page.claims], ['professional-role', 'likes-short-emails'])
        first = page.claims[0]
        self.assertEqual(first.fields['claim'], 'The user is a Field CTO at an IT consulting firm.')
        self.assertEqual(first.fields['status'], 'confirmed')
        self.assertEqual(first.fields['review by'], 'Next role-related update.')
        self.assertEqual([(l.target, l.fragment) for l in first.evidence_links], [('sources/2026-11-08-role.md', None)])
        self.assertEqual(page.claims[1].evidence_links[0].fragment, 'derived-claims')

    def test_feedback_callouts(self):
        page = parse_page(FIXTURE, 'memory/feedback.md')
        self.assertEqual(len(page.feedback), 1)
        fb = page.feedback[0]
        self.assertEqual(fb.date, '2026-11-10')
        self.assertEqual(fb.claim_target, ('identity/profile.md', 'likes-short-emails'))
        self.assertEqual(fb.scope, 'Email to clients')
        self.assertTrue(fb.applied)
        self.assertEqual(fb.text, 'Keep them short, but always end with the next step.')


if __name__ == '__main__':
    unittest.main()
