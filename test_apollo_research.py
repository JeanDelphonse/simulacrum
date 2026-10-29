"""Apollo prospect sourcing against the current Apollo API (mocked HTTP).

Apollo's People API Search (POST /api/v1/mixed_people/api_search) returns no
email addresses — only has_email, a first name and an obfuscated last name.
Emails come from Bulk People Enrichment (POST /api/v1/people/bulk_match,
credits). The old code called /v1/mixed_people/search and dropped every person
without an ``email`` field, so Apollo contributed no prospects.

Runs on in-memory SQLite; requests.post is faked, nothing leaves the machine.

    python test_apollo_research.py
"""
import os
import sys

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from utils.id_gen import generate_id                          # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    line = '  {} {}{}'.format(mark, label, ('  -- ' + str(detail)) if detail else '')
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def search_person(pid, first, company, has_email=True):
    return {'id': pid, 'first_name': first, 'last_name_obfuscated': 'Sm***h',
            'title': 'VP Operations', 'has_email': has_email,
            'organization': {'name': company, 'has_employee_count': True}}


SEARCH_PAGE = [
    search_person('p1', 'Ann', 'Alpha Co'),
    search_person('p2', 'Bob', 'Beta Co', has_email=False),      # nothing to enrich
    search_person('p3', 'Cal', 'Known Co'),                      # already in the CRM
    search_person('p4', 'Dee', 'Delta Co'),
    search_person('p5', 'Eve', 'Echo Co'),                       # email stays locked
]
ENRICHED = {
    'p1': {'id': 'p1', 'first_name': 'Ann', 'last_name': 'Smith', 'email': 'ann@alpha.co',
           'email_status': 'verified', 'title': 'VP Operations',
           'organization': {'name': 'Alpha Co', 'website_url': 'alpha.co'}},
    'p4': {'id': 'p4', 'first_name': 'Dee', 'last_name': 'Dunn', 'email': 'dee@delta.co',
           'email_status': 'extrapolated', 'title': 'COO',
           'organization': {'name': 'Delta Co'}},
    'p5': {'id': 'p5', 'first_name': 'Eve', 'last_name': 'Erb',
           'email': 'email_not_unlocked@domain.com', 'organization': {'name': 'Echo Co'}},
}


def main():
    import requests
    calls = []

    def fake_post(url, headers=None, json=None, params=None, timeout=None):
        calls.append({'url': url, 'headers': headers, 'json': json, 'params': params,
                      'timeout': timeout})
        if url.endswith('/mixed_people/api_search'):
            return FakeResponse({'people': SEARCH_PAGE, 'total_entries': len(SEARCH_PAGE)})
        if url.endswith('/people/bulk_match'):
            ids = [d['id'] for d in json['details']]
            return FakeResponse({'matches': [ENRICHED.get(i) for i in ids],
                                 'credits_consumed': sum(1 for i in ids if i in ENRICHED)})
        raise AssertionError('unexpected Apollo call ' + url)

    requests.post = fake_post

    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    from cryptography.fernet import Fernet
    app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()   # test-only key

    with app.app_context():
        db.create_all()
        from app.models.user import User
        from app.models.contact import Contact
        from app.models.integration import UserIntegration
        from app.services.apollo_client import (
            ApolloClient, client_for, normalize_employee_ranges, normalize_seniorities)
        from app.services.prospect_research_engine import (
            ProspectResearchEngine, build_targeting_criteria)
        from app.services.token_crypto import encrypt_token

        print('\n1. Request shape')
        check('employee ranges converted to Apollo "min,max"',
              normalize_employee_ranges(['51-200', '201-500', '10001+']) ==
              ['51,200', '201,500', '10001,1000000'])
        check('seniorities mapped to Apollo values, unknown ones dropped',
              normalize_seniorities(['vp', 'principal', 'c_suite', 'bogus']) == ['vp', 'partner', 'c_suite'])
        ApolloClient('key-123').people_search(
            person_titles=['VP Operations'], person_seniorities=['vp', 'principal'],
            organization_num_employees_ranges=['51-200'], person_locations=['United States'],
            per_page=25, page=2)
        c = calls[-1]
        check('uses the current People API Search endpoint',
              c['url'] == 'https://api.apollo.io/api/v1/mixed_people/api_search', c['url'])
        check('filters sent as query parameters in Apollo formats',
              c['params']['organization_num_employees_ranges[]'] == ['51,200']
              and c['params']['person_seniorities[]'] == ['vp', 'partner']
              and c['params']['page'] == 2, c['params'])
        check('API key sent as X-Api-Key', c['headers'].get('X-Api-Key') == 'key-123')
        check('every Apollo call has a timeout', c['timeout'] and c['timeout'] <= 30, c['timeout'])

        user = User(id=generate_id(), email='o@x.com', full_name='O', password_hash='x')
        db.session.add(user)
        db.session.flush()
        rec = UserIntegration(id=generate_id(), user_id=user.id, provider='apollo')
        rec.access_token_enc = encrypt_token('key-abc')
        db.session.add(rec)
        db.session.add(Contact(id=generate_id(), user_id=user.id, first_name='Cal', last_name='K',
                               email='cal@known.co', company_name='Known Co', source='manual'))
        db.session.commit()

        oauth = UserIntegration(id=generate_id(), user_id=user.id, provider='apollo_oauth_test')
        oauth.access_token_enc = encrypt_token('tok')
        oauth.refresh_token_enc = encrypt_token('refresh')
        check('OAuth connections use a Bearer header',
              client_for(oauth)._headers().get('Authorization') == 'Bearer tok')
        check('API-key connections use X-Api-Key', client_for(rec)._headers().get('X-Api-Key') == 'key-abc')

        print('\n2. Search → enrich → prospects')
        calls.clear()
        eng = ProspectResearchEngine()
        eng._deadline = float('inf')
        targeting = build_targeting_criteria('cold_email_campaign', 'Operations Consulting', {})
        prospects = eng._search_apollo(user.id, targeting, count=5)
        enrich_calls = [x for x in calls if x['url'].endswith('/people/bulk_match')]
        enriched_ids = [d['id'] for x in enrich_calls for d in x['json']['details']]
        check('only people with has_email and not already in the CRM are enriched',
              enriched_ids == ['p1', 'p4', 'p5'], enriched_ids)
        check('enrichment does not request personal emails or phones',
              all(x['json']['reveal_personal_emails'] is False and x['json']['reveal_phone_number'] is False
                  for x in enrich_calls))
        emails = [p.email for p in prospects]
        check('enriched people become prospects with their work email',
              emails == ['ann@alpha.co', 'dee@delta.co'], emails)
        check('locked placeholder emails are never used', 'email_not_unlocked@domain.com' not in emails)
        check('verified vs extrapolated email status carried over',
              [p.email_verified for p in prospects] == [True, False])
        check('real last names come from enrichment, not the obfuscated search value',
              [p.last_name for p in prospects] == ['Smith', 'Dunn'])
        s = eng.apollo_stats
        check('run stats recorded for the log', s['searched'] >= 5 and s['known'] >= 1
              and s['credits'] >= 2 and s['found'] == 2, s)

        calls.clear()
        eng2 = ProspectResearchEngine()
        eng2._deadline = float('inf')
        eng2._exclude = {'ann@alpha.co'}
        got = [p.email for p in eng2._search_apollo(user.id, targeting, count=5)]
        check('people the agent already targeted are dropped after enrichment',
              got == ['dee@delta.co'], got)

        calls.clear()
        eng3 = ProspectResearchEngine()
        eng3._deadline = float('inf')
        eng3._search_apollo(user.id, targeting, count=1)
        n = sum(len(x['json']['details']) for x in calls if x['url'].endswith('/people/bulk_match'))
        check('credit use is capped (count=1 enriches at most 10)', n <= 10, n)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
