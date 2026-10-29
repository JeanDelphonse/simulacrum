import logging
from urllib.parse import urlencode
from flask import current_app

logger = logging.getLogger(__name__)

APOLLO_BASE_URL      = 'https://api.apollo.io/v1'
# Current API base for People API Search and enrichment (docs.apollo.io).
APOLLO_API_URL       = 'https://api.apollo.io/api/v1'
# Every Apollo call is bounded: a stalled request would otherwise hold an agent
# run (and the one-at-a-time cycle runner) indefinitely.
REQUEST_TIMEOUT      = 20

# People API Search accepts only these seniorities.
VALID_SENIORITIES = frozenset({'owner', 'founder', 'c_suite', 'partner', 'vp', 'head',
                               'director', 'manager', 'senior', 'entry', 'intern'})
_SENIORITY_ALIASES = {'principal': 'partner', 'executive': 'c_suite', 'cxo': 'c_suite'}
APOLLO_AUTH_URL      = 'https://api.apollo.io/oauth2/authorize'
APOLLO_TOKEN_URL     = 'https://api.apollo.io/oauth2/token'
APOLLO_SCOPES        = 'sequences:write contacts:write emailer_campaigns:write webhooks:write'


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def get_auth_url(state: str) -> str:
    params = {
        'response_type': 'code',
        'client_id': current_app.config['APOLLO_CLIENT_ID'],
        'redirect_uri': current_app.config['APOLLO_REDIRECT_URI'],
        'scope': APOLLO_SCOPES,
        'state': state,
    }
    return f"{APOLLO_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    import requests
    resp = requests.post(APOLLO_TOKEN_URL, json={
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': current_app.config['APOLLO_REDIRECT_URI'],
        'client_id': current_app.config['APOLLO_CLIENT_ID'],
        'client_secret': current_app.config['APOLLO_CLIENT_SECRET'],
    })
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    import requests
    resp = requests.post(APOLLO_TOKEN_URL, json={
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': current_app.config['APOLLO_CLIENT_ID'],
        'client_secret': current_app.config['APOLLO_CLIENT_SECRET'],
    })
    resp.raise_for_status()
    return resp.json()


# ── API client ────────────────────────────────────────────────────────────────

class ApolloClient:
    def __init__(self, access_token: str, auth_type: str = 'api_key'):
        self._token = access_token
        self._auth_type = auth_type

    def _headers(self):
        if self._auth_type == 'oauth':
            return {'Authorization': f'Bearer {self._token}', 'Content-Type': 'application/json'}
        return {'X-Api-Key': self._token, 'Content-Type': 'application/json'}

    def _get(self, path, params=None):
        import requests
        resp = requests.get(f'{APOLLO_BASE_URL}{path}', headers=self._headers(), params=params,
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, body=None, params=None, base=APOLLO_BASE_URL):
        import requests
        resp = requests.post(f'{base}{path}', headers=self._headers(), json=body or {},
                             params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # ── Contacts ──────────────────────────────────────────────────────────────

    def upsert_contact(self, email, first_name, last_name, title=None,
                       company=None, custom_fields=None):
        payload = {
            'email': email,
            'first_name': first_name,
            'last_name': last_name,
        }
        if title:
            payload['title'] = title
        if company:
            payload['organization_name'] = company
        if custom_fields:
            payload['custom_fields'] = custom_fields
        data = self._post('/contacts/upsert', payload)
        return data.get('contact', {})

    # ── Sequences ─────────────────────────────────────────────────────────────

    def create_sequence(self, name: str, steps: list, daily_limit: int = 30) -> dict:
        payload = {
            'name': name,
            'active': True,
            'daily_limit': daily_limit,
            'steps': steps,
        }
        data = self._post('/emailer_campaigns', payload)
        return data.get('emailer_campaign', {})

    def add_contacts_to_sequence(self, sequence_id: str, contact_ids: list):
        payload = {
            'emailer_campaign_id': sequence_id,
            'contact_ids': contact_ids,
        }
        return self._post('/emailer_campaigns/add_contact_ids', payload)

    # ── Webhooks ──────────────────────────────────────────────────────────────

    def register_webhook(self, url: str, events: list, sequence_id: str = None):
        payload = {
            'notification_endpoint_url': url,
            'event_types': events,
        }
        if sequence_id:
            payload['emailer_campaign_id'] = sequence_id
        return self._post('/webhooks', payload)

    # ── People Search (FR-RESEARCH-02) ───────────────────────────────────────

    def people_search(
        self,
        person_titles: list = None,
        person_seniorities: list = None,
        organization_num_employees_ranges: list = None,
        organization_industry_tag_ids: list = None,
        person_locations: list = None,
        per_page: int = 25,
        page: int = 1,
    ) -> list:
        """
        Apollo People API Search (POST /api/v1/mixed_people/api_search).

        Returns people WITHOUT email addresses — Apollo's search never returns
        them, only a ``has_email`` flag, a first name and an obfuscated last
        name. Use bulk_enrich() on the ``id``s to get emails. The old
        /v1/mixed_people/search call this replaced assumed an ``email`` field,
        so every Apollo result was silently dropped. Search uses no credits.
        Filters go in the query string (the API defines no request body).
        """
        params = {'per_page': max(1, min(int(per_page or 25), 100)),
                  'page': max(1, min(int(page or 1), 500))}
        if person_titles:
            params['person_titles[]'] = list(person_titles)
        seniorities = normalize_seniorities(person_seniorities)
        if seniorities:
            params['person_seniorities[]'] = seniorities
        ranges = normalize_employee_ranges(organization_num_employees_ranges)
        if ranges:
            params['organization_num_employees_ranges[]'] = ranges
        if organization_industry_tag_ids:
            params['organization_industry_tag_ids[]'] = list(organization_industry_tag_ids)
        if person_locations:
            params['person_locations[]'] = list(person_locations)

        data = self._post('/mixed_people/api_search', params=params, base=APOLLO_API_URL)
        return data.get('people', []) or []

    def bulk_enrich(self, person_ids: list) -> tuple:
        """Emails for people found by people_search (POST /api/v1/people/bulk_match).

        Up to 10 people per request; consumes credits (about 1 per person with
        an email found). Personal emails are not requested. Returns
        (matches, credits_consumed) with unmatched entries dropped.
        """
        matches, credits = [], 0
        ids = [i for i in person_ids if i]
        for i in range(0, len(ids), 10):
            data = self._post('/people/bulk_match', base=APOLLO_API_URL, body={
                'details': [{'id': pid} for pid in ids[i:i + 10]],
                'reveal_personal_emails': False,
                'reveal_phone_number': False,
            })
            matches += [m for m in (data.get('matches') or []) if m]
            credits += int(data.get('credits_consumed') or 0)
        return matches, credits

    # ── Company Search (SIM-PRD-CRM-002 FR-DSC-02) ───────────────────────────

    def organization_search(
        self,
        keywords: list = None,
        num_employees_ranges: list = None,
        locations: list = None,
        industry_tag_ids: list = None,
        per_page: int = 25,
        page: int = 1,
    ) -> list:
        """Query Apollo for COMPANIES matching firmographic filters.

        Distinct from people_search: discovery needs firms, not contacts. Apollo
        can filter by size/industry/geo but cannot judge fit — that is the Claude
        scoring layer in discovery_service.

        Returns a list of organization dicts. Apollo splits results between
        'organizations' (its global database) and 'accounts' (records already in
        the workspace); both are real matches, so both are returned.
        """
        payload = {'per_page': per_page, 'page': page}
        if keywords:
            payload['q_organization_keyword_tags'] = keywords
        if num_employees_ranges:
            payload['organization_num_employees_ranges'] = num_employees_ranges
        if locations:
            payload['organization_locations'] = locations
        if industry_tag_ids:
            payload['organization_industry_tag_ids'] = industry_tag_ids

        data = self._post('/mixed_companies/search', payload)
        return (data.get('organizations') or []) + (data.get('accounts') or [])

    # ── Sequence pause/cancel ─────────────────────────────────────────────────

    def pause_sequence(self, sequence_id: str):
        return self._post(f'/emailer_campaigns/{sequence_id}/pause')

    def cancel_sequence(self, sequence_id: str):
        return self._post(f'/emailer_campaigns/{sequence_id}/archive')


# ── Shared helpers ────────────────────────────────────────────────────────────

def client_for(rec) -> 'ApolloClient':
    """ApolloClient for a stored UserIntegration with the right auth header.

    An OAuth token must go out as a Bearer header; sending it as X-Api-Key gets
    a 403. A refresh token is how the app tells the two apart (API keys saved by
    the connect wizard have none).
    """
    from app.services.token_crypto import decrypt_token
    auth_type = 'oauth' if getattr(rec, 'refresh_token_enc', None) else 'api_key'
    return ApolloClient(decrypt_token(rec.access_token_enc), auth_type=auth_type)


def normalize_employee_ranges(ranges) -> list:
    """Apollo wants "51,200" (comma, no spaces); targeting uses "51-200" / "10001+"."""
    out = []
    for r in ranges or []:
        r = str(r).strip().replace(' ', '')
        if r.endswith('+'):
            r = r[:-1] + ',1000000'
        r = r.replace('-', ',')
        if r and r not in out:
            out.append(r)
    return out


def normalize_seniorities(values) -> list:
    out = []
    for v in values or []:
        v = _SENIORITY_ALIASES.get(str(v).strip().lower(), str(v).strip().lower())
        if v in VALID_SENIORITIES and v not in out:
            out.append(v)
    return out

