"""Node and relation tables of the Duet graph: the Model step."""
COMMON = ('id STRING PRIMARY KEY, label STRING, scope STRING, owner STRING, created STRING, '
          'updated STRING, source_locator STRING, defined_in STRING, history STRING[]')

NODE_TABLES = {
    'Person': 'is_karteek BOOLEAN, role STRING',
    'Claim': 'status STRING, scope_text STRING, recorded STRING, review_by STRING, exceptions STRING',
    'Source': 'kind STRING, date STRING, origin STRING, provider STRING',
    'Feedback': 'date STRING, scope_text STRING, applied BOOLEAN',
    'Account': 'stage STRING',
    'Meeting': 'start STRING, provider_ids STRING[], summary_hash STRING',
    'Decision': 'date STRING, rationale STRING, reopen_when STRING',
    'Commitment': 'due STRING, status STRING, owner_known BOOLEAN',
    'Concept': 'author STRING',
    'Document': 'path STRING, hash STRING, frontmatter_type STRING',
    'Playbook': 'area STRING, execution STRING',
    'DuetItem': 'key STRING, revision STRING, status STRING, snooze_until STRING, why_now STRING, draft STRING',
}

REL_TABLES = {
    'SUPPORTED_BY': [('Claim', 'Source'), ('Decision', 'Source'), ('Commitment', 'Source'), ('Meeting', 'Source')],
    'SUPERSEDES': [('Claim', 'Claim')],
    'CORRECTS': [('Feedback', 'Claim')],
    'ABOUT': [('Claim', 'Person'), ('Claim', 'Account')],
    'ATTENDED': [('Person', 'Meeting')],
    'DECIDED_IN': [('Decision', 'Meeting')],
    'COMMITTED_BY': [('Commitment', 'Person')],
    'CONCERNS': [('Meeting', 'Account'), ('Decision', 'Account'), ('Commitment', 'Account')],
    'MENTIONS': [('Document', table) for table in NODE_TABLES],
    'LINKS_TO': [('Document', 'Document')],
    'ATTRIBUTED_TO': [('Concept', 'Person')],
    'DERIVED_FROM': [('Concept', 'Document')],
    'USES': [('DuetItem', 'Playbook')],
    'RESTS_ON': [('DuetItem', 'Meeting'), ('DuetItem', 'Commitment'), ('DuetItem', 'Decision'), ('DuetItem', 'Claim')],
}
REL_PROPS = 'locator STRING, created STRING'

STATUSES = ('confirmed', 'hypothesis', 'unknown', 'superseded')
EVIDENCE_TABLES = ('Claim', 'Decision', 'Commitment', 'Meeting')
ACTORS = ('indexer', 'mcp:claude')
SCOPES = ('tracked', 'private')


def ddl():
    """Idempotent CREATE statements for every table."""
    statements = [f'CREATE NODE TABLE IF NOT EXISTS {name}({COMMON}, {props})' for name, props in NODE_TABLES.items()]
    for name, pairs in REL_TABLES.items():
        froms = ', '.join(f'FROM {a} TO {b}' for a, b in pairs)
        statements.append(f'CREATE REL TABLE IF NOT EXISTS {name}({froms}, {REL_PROPS})')
    return statements
