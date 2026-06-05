"""
Prospect Research Engine — SIM-PRD-RESEARCH-001
Five-stage pipeline: Apollo → Web Search → Email Discovery → Verification → CRM Merge
Called by outreach agents at dispatch time. Never called directly by users.
"""
import json
import time
import logging
import re
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from flask import current_app

from app.extensions import db
from app.models.prospect_research import (
    TargetingCriteria, Prospect, ProspectList, ProspectResearchRun,
)
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

# ── Source / expertise mappings ───────────────────────────────────────────────

# Maps lowercase expertise tag keywords → ordered list of source categories to search
EXPERTISE_TO_SOURCES: dict[str, list[str]] = {
    'government':     ['company_websites', 'government_registries', 'job_boards'],
    'public_sector':  ['company_websites', 'government_registries', 'job_boards'],
    'defense':        ['company_websites', 'government_registries', 'job_boards'],
    'healthcare':     ['company_websites', 'government_registries', 'professional_associations', 'industry_databases'],
    'pharma':         ['company_websites', 'industry_databases', 'professional_associations'],
    'construction':   ['company_websites', 'government_registries', 'job_boards'],
    'infrastructure': ['company_websites', 'government_registries', 'job_boards'],
    'hr':             ['company_websites', 'professional_associations', 'job_boards'],
    'talent':         ['company_websites', 'professional_associations', 'job_boards'],
    'recruiting':     ['company_websites', 'professional_associations', 'job_boards'],
    'marketing':      ['company_websites', 'professional_associations', 'job_boards', 'conferences'],
    'brand':          ['company_websites', 'professional_associations', 'conferences'],
    'design':         ['company_websites', 'professional_associations', 'conferences'],
    'engineering':    ['company_websites', 'professional_associations', 'job_boards'],
    'finance':        ['company_websites', 'professional_associations', 'industry_databases'],
    'investment':     ['company_websites', 'industry_databases'],
    'venture':        ['company_websites', 'industry_databases'],
    'startup':        ['company_websites', 'industry_databases', 'community_forums'],
    'saas':           ['company_websites', 'industry_databases', 'job_boards'],
    'speaking':       ['conferences', 'company_websites', 'community_forums'],
    'thought_leadership': ['conferences', 'community_forums', 'company_websites'],
    'nonprofit':      ['company_websites', 'industry_databases', 'professional_associations'],
    'real_estate':    ['company_websites', 'industry_databases', 'government_registries'],
    '_default':       ['company_websites', 'job_boards'],
}

EXPERTISE_TO_ASSOCIATION: dict[str, str] = {
    'hr':                 'SHRM',
    'talent':             'SHRM',
    'marketing':          'AMA',
    'design':             'AIGA',
    'project_management': 'PMI',
    'engineering':        'IEEE',
    'healthcare':         'AMA',
    'finance':            'CFA Institute',
}

EXPERTISE_TO_DATABASE: dict[str, str] = {
    'startup':    'crunchbase',
    'venture':    'crunchbase',
    'saas':       'crunchbase',
    'investment': 'pitchbook',
    'healthcare': 'clinicaltrials',
    'pharma':     'clinicaltrials',
    'real_estate': 'county_assessor',
    'nonprofit':  'guidestar',
}

# B2B email patterns ordered by prevalence
EMAIL_PATTERNS = [
    '{first}.{last}@{domain}',
    '{first}{last}@{domain}',
    '{f}{last}@{domain}',
    '{first}@{domain}',
    '{first}_{last}@{domain}',
    '{last}.{first}@{domain}',
    '{f}.{last}@{domain}',
]

COMPANY_SIZE_TO_APOLLO_RANGE: dict[str, list[str]] = {
    'startup':    ['1-10', '11-50'],
    'SMB':        ['51-200', '201-500'],
    'mid-market': ['501-1000', '1001-5000'],
    'enterprise': ['5001-10000', '10001+'],
    'all':        ['51-200', '201-500', '501-1000'],
}

# Agents that call the research engine (alumni_reactivation excluded per FR-RESEARCH-11)
RESEARCH_ENABLED_AGENTS = {
    'cold_email_campaign',
    'outreach_email',
    'role_search',
    'referral_network',
    'speaking_proposals',
    'corporate_training_proposal',
}


# ── Targeting builders (per-agent layer) ─────────────────────────────────────

def _extract_expertise_tags(expertise_zone: str) -> list[str]:
    """Extract lowercase keyword tags from an expertise zone name."""
    stop = {'and', 'or', 'the', 'a', 'an', 'for', 'of', 'in', 'at', 'to', 'with'}
    words = re.findall(r'[a-zA-Z]+', expertise_zone.lower())
    return [w for w in words if w not in stop and len(w) > 2]


def _titles_for_zone(tags: list[str]) -> list[str]:
    tag_set = set(tags)
    if tag_set & {'hr', 'talent', 'recruiting', 'people'}:
        return ['VP People', 'CHRO', 'Head of Talent', 'VP HR', 'Director of HR']
    if tag_set & {'marketing', 'brand', 'growth'}:
        return ['VP Marketing', 'CMO', 'Head of Brand', 'Director of Marketing', 'Head of Growth']
    if tag_set & {'design', 'ux', 'product'}:
        return ['VP Design', 'Head of Design', 'Director of UX', 'CPO', 'Head of Product']
    if tag_set & {'engineering', 'tech', 'software', 'data'}:
        return ['CTO', 'VP Engineering', 'Head of Engineering', 'Director of Engineering']
    if tag_set & {'finance', 'investment', 'venture', 'capital'}:
        return ['CFO', 'VP Finance', 'Managing Director', 'Partner', 'Principal']
    if tag_set & {'sales', 'revenue', 'business', 'development'}:
        return ['VP Sales', 'CRO', 'Head of Business Development', 'Director of Sales']
    if tag_set & {'operations', 'strategy', 'consulting'}:
        return ['COO', 'VP Operations', 'Head of Strategy', 'Director of Operations']
    return ['VP', 'Director', 'Head of', 'C-Suite Executive']


def _adjacent_titles_for_zone(tags: list[str]) -> list[str]:
    """Titles for referral_network — adjacent professionals, not buyers."""
    tag_set = set(tags)
    if tag_set & {'marketing', 'brand'}:
        return ['Copywriter', 'Web Developer', 'SEO Consultant', 'Marketing Strategist', 'PR Consultant']
    if tag_set & {'hr', 'talent', 'coaching'}:
        return ['Executive Coach', 'HR Consultant', 'Organizational Psychologist', 'Career Coach']
    if tag_set & {'design'}:
        return ['Copywriter', 'Brand Strategist', 'Web Developer', 'Marketing Consultant']
    if tag_set & {'finance', 'accounting'}:
        return ['Business Attorney', 'CPA', 'Financial Advisor', 'Business Broker']
    return ['Business Consultant', 'Executive Coach', 'Management Consultant', 'Advisor']


def _industries_for_zone(tags: list[str]) -> list[str]:
    tag_set = set(tags)
    if tag_set & {'healthcare', 'pharma', 'medical'}:
        return ['healthcare', 'pharmaceuticals', 'biotechnology']
    if tag_set & {'finance', 'investment', 'banking'}:
        return ['financial services', 'investment management', 'banking']
    if tag_set & {'saas', 'software', 'tech', 'engineering'}:
        return ['software', 'information technology', 'internet']
    if tag_set & {'marketing', 'brand', 'advertising'}:
        return ['marketing and advertising', 'media', 'public relations']
    if tag_set & {'education', 'training', 'coaching'}:
        return ['education management', 'e-learning', 'professional training']
    if tag_set & {'real_estate', 'property'}:
        return ['real estate', 'commercial real estate']
    return ['professional services', 'business consulting', 'management consulting']


def _industries_from_text(text: str) -> list[str]:
    if not text:
        return ['professional services']
    return [t.strip().lower() for t in re.split(r'[,;/]', text) if t.strip()][:5]


def build_targeting_criteria(
    action_type: str,
    expertise_zone: str,
    user_inputs: dict,
) -> TargetingCriteria:
    """Build per-agent targeting criteria from expertise zone + user inputs."""
    tags = _extract_expertise_tags(expertise_zone)

    if action_type == 'cold_email_campaign':
        size_key = user_inputs.get('target_company_size', 'SMB')
        return TargetingCriteria(
            expertise_zone=expertise_zone,
            expertise_tags=tags,
            job_titles=_titles_for_zone(tags),
            seniorities=['vp', 'c_suite', 'director'],
            company_sizes=COMPANY_SIZE_TO_APOLLO_RANGE.get(size_key, ['51-200', '201-500']),
            industries=_industries_for_zone(tags),
            geographies=['United States'],
            pain_point=user_inputs.get('pain_point', ''),
            agent_type=action_type,
        )

    if action_type == 'outreach_email':
        return TargetingCriteria(
            expertise_zone=expertise_zone,
            expertise_tags=tags,
            job_titles=_titles_for_zone(tags),
            seniorities=['vp', 'c_suite', 'director'],
            company_sizes=['51-200', '201-500'],
            industries=_industries_from_text(user_inputs.get('target_industries', '')),
            geographies=['United States'],
            agent_type=action_type,
        )

    if action_type == 'consulting_outreach':
        industries_raw = user_inputs.get('target_industries', '')
        return TargetingCriteria(
            expertise_zone=expertise_zone,
            expertise_tags=tags,
            job_titles=_titles_for_zone(tags),
            seniorities=['vp', 'c_suite', 'director'],
            company_sizes=['51-200', '201-500', '501-1000'],
            industries=_industries_from_text(industries_raw) if industries_raw else _industries_for_zone(tags),
            geographies=['United States'],
            agent_type=action_type,
        )

    if action_type == 'speaking_proposals':
        geo = user_inputs.get('geographic_regions', 'United States') or 'United States'
        return TargetingCriteria(
            expertise_zone=expertise_zone,
            expertise_tags=tags + ['speaking', 'thought_leadership'],
            job_titles=[
                'Conference Organizer', 'Program Director', 'Event Coordinator',
                'Podcast Host', 'Summit Producer', 'Meetup Organizer',
            ],
            seniorities=['manager', 'director', 'owner'],
            company_sizes=['1-10', '11-50'],
            industries=_industries_for_zone(tags),
            geographies=[geo],
            agent_type=action_type,
        )

    if action_type == 'corporate_training_proposal':
        size_key = user_inputs.get('target_company_sizes', 'mid-market')
        buyer_depts = user_inputs.get('buyer_departments', 'HR, L&D')
        dept_titles = [f'VP {d.strip()}' for d in buyer_depts.split(',')[:2]]
        return TargetingCriteria(
            expertise_zone=expertise_zone,
            expertise_tags=tags,
            job_titles=[
                'VP People', 'CHRO', 'Head of L&D', 'VP Learning',
                'Director of Training & Development', 'CLO',
            ] + dept_titles,
            seniorities=['vp', 'c_suite', 'director'],
            company_sizes=COMPANY_SIZE_TO_APOLLO_RANGE.get(size_key, ['1001-5000', '5001-10000']),
            industries=_industries_for_zone(tags),
            geographies=['United States'],
            agent_type=action_type,
        )

    if action_type == 'referral_network':
        return TargetingCriteria(
            expertise_zone=expertise_zone,
            expertise_tags=tags,
            job_titles=_adjacent_titles_for_zone(tags),
            seniorities=['owner', 'partner', 'principal', 'director'],
            company_sizes=['1-10', '11-50', '51-200'],
            industries=_industries_for_zone(tags),
            geographies=['United States'],
            agent_type=action_type,
        )

    if action_type == 'role_search':
        geo = user_inputs.get('location_preference', 'United States') or 'United States'
        return TargetingCriteria(
            expertise_zone=expertise_zone,
            expertise_tags=tags,
            job_titles=_titles_for_zone(tags),
            seniorities=['vp', 'director', 'manager'],
            company_sizes=['51-200', '201-500', '501-1000'],
            industries=_industries_for_zone(tags),
            geographies=[geo],
            agent_type=action_type,
        )

    # Fallback
    return TargetingCriteria(
        expertise_zone=expertise_zone,
        expertise_tags=tags,
        job_titles=_titles_for_zone(tags),
        seniorities=['vp', 'director', 'c_suite'],
        company_sizes=['51-200', '201-500'],
        industries=_industries_for_zone(tags),
        geographies=['United States'],
        agent_type=action_type,
    )


# ── Email verifier abstraction ────────────────────────────────────────────────

class _VerificationResult:
    def __init__(self, status: str, confidence: float = 0.0, risk_reason: str = None):
        self.status = status           # 'valid' | 'risky' | 'invalid' | 'unknown'
        self.confidence = confidence
        self.risk_reason = risk_reason


def _get_verifier_config() -> tuple[str, str]:
    from app.models.platform_settings import PlatformSetting
    provider = PlatformSetting.get('email_verifier_provider', 'hunter')
    api_key  = PlatformSetting.get('email_verifier_api_key', '')
    return provider, api_key


def _verify_one_email(email: str, provider: str, api_key: str) -> _VerificationResult:
    """Call the configured verification API for a single email address."""
    import requests as _req
    try:
        if provider == 'hunter':
            r = _req.get(
                'https://api.hunter.io/v2/email-verifier',
                params={'email': email, 'api_key': api_key},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json().get('data', {})
            status = data.get('status', 'unknown')
            score  = data.get('score', 0) / 100.0
            if status == 'valid':
                return _VerificationResult('valid', score)
            if status in ('risky', 'accept_all'):
                return _VerificationResult('risky', score, 'catch-all or risky domain')
            if status == 'invalid':
                return _VerificationResult('invalid', 0.0)
            return _VerificationResult('unknown', 0.0)

        if provider == 'zerobounce':
            r = _req.get(
                'https://api.zerobounce.net/v2/validate',
                params={'apikey': api_key, 'email': email, 'ip_address': ''},
                timeout=10,
            )
            r.raise_for_status()
            data   = r.json()
            status = data.get('status', 'unknown')
            if status == 'valid':
                return _VerificationResult('valid', 0.95)
            if status in ('catch-all', 'unknown'):
                return _VerificationResult('risky', 0.5, status)
            if status == 'invalid':
                return _VerificationResult('invalid', 0.0)
            return _VerificationResult('unknown', 0.0)

        if provider == 'neverbounce':
            r = _req.post(
                'https://api.neverbounce.com/v4/single/check',
                json={'api_key': api_key, 'email': email},
                timeout=10,
            )
            r.raise_for_status()
            data   = r.json()
            result = data.get('result', 'unknown')
            if result == 'valid':
                return _VerificationResult('valid', 0.95)
            if result in ('catchall', 'unknown'):
                return _VerificationResult('risky', 0.5, result)
            if result in ('invalid', 'disposable'):
                return _VerificationResult('invalid', 0.0)
            return _VerificationResult('unknown', 0.0)

    except Exception as exc:
        logger.warning('Email verification failed for %s (%s): %s', email, provider, exc)
        return _VerificationResult('unknown', 0.0)

    return _VerificationResult('unknown', 0.0)


def _check_monthly_budget() -> bool:
    """Return True if monthly verification budget has NOT been exhausted.

    Retries once on OperationalError (2006 MySQL server has gone away).
    The connection can time out while Claude API calls are in-flight because
    it is held by the session rather than returned to the pool, so pool_pre_ping
    cannot protect it.  A close() + retry forces a fresh checkout with pre-ping.
    """
    from app.models.platform_settings import PlatformSetting
    from sqlalchemy import func, extract
    from sqlalchemy.exc import OperationalError

    def _run() -> bool:
        cap_cents = int(PlatformSetting.get('email_verifier_monthly_budget_cents', '5000'))
        if cap_cents <= 0:
            return True
        now = datetime.utcnow()
        spent = db.session.query(
            func.coalesce(func.sum(ProspectResearchRun.verification_cost_cents), 0)
        ).filter(
            extract('year',  ProspectResearchRun.created_at) == now.year,
            extract('month', ProspectResearchRun.created_at) == now.month,
        ).scalar() or 0
        return int(spent) < cap_cents

    try:
        return _run()
    except OperationalError:
        # Connection timed out while idle — return it to the pool, then retry.
        try:
            db.session.close()
        except Exception:
            pass
        try:
            return _run()
        except Exception as exc:
            logger.warning('_check_monthly_budget retry failed: %s — assuming budget OK', exc)
            return True  # non-fatal; allow research to continue


# ── Main engine ───────────────────────────────────────────────────────────────

class ProspectResearchEngine:
    """
    Shared prospect research sub-agent.
    Called by outreach agents — never directly by users.
    """

    def __init__(self):
        self._sources_log: list[str] = []
        self._total_researched: int  = 0
        self._discarded_count: int   = 0
        self._from_apollo: int       = 0
        self._from_web: int          = 0
        self._from_crm: int          = 0
        self._risky_count: int       = 0
        self._verification_cents: int = 0
        self._apollo_calls: int      = 0
        self._web_search_calls: int  = 0
        self._extraction_calls: int  = 0

    def research(
        self,
        user_id: str,
        simulation_id: str,
        action_id: str,
        targeting: TargetingCriteria,
        target_count: int = 25,
        agent_context: dict = None,
    ) -> ProspectList:
        t_start = time.time()
        prospects = []

        # Stage 1: Apollo People Search
        if self._has_integration(user_id, 'apollo'):
            try:
                apollo_results = self._search_apollo(user_id, targeting, target_count)
                prospects.extend(apollo_results)
                self._from_apollo = len(apollo_results)
                if apollo_results:
                    self._sources_log.append('apollo')
            except Exception as exc:
                logger.error('Apollo search failed for user %s: %s', user_id, exc)

        # Stage 2: Web search for shortfall
        shortfall = target_count - len(prospects)
        if shortfall > 0:
            try:
                web_results = self._search_web(targeting, shortfall, agent_context)
                prospects.extend(web_results)
                self._from_web = len(web_results)
            except Exception as exc:
                logger.error('Web search stage failed: %s', exc)

        self._total_researched = len(prospects)

        # Stage 3: Email discovery for prospects without emails
        for p in prospects:
            if not p.email and not p.email_candidates:
                try:
                    p.email_candidates = self._discover_email(p)
                except Exception as exc:
                    logger.debug('Email discovery failed for %s: %s', p.display_name, exc)

        # Stage 4: Email verification
        budget_ok = _check_monthly_budget()
        if not budget_ok:
            logger.warning('Monthly email verification budget exhausted — skipping verification')
        prospects = self._verify_emails(prospects, skip_verification=not budget_ok)

        # Stage 5: CRM merge
        prospects = self._merge_with_crm(user_id, simulation_id, action_id, prospects, targeting)

        duration = round(time.time() - t_start, 2)

        # Persist run log
        try:
            self._log_run(
                user_id, simulation_id, action_id, targeting, duration,
            )
        except Exception as exc:
            logger.warning('Failed to log research run: %s', exc)
            try:
                db.session.rollback()
            except Exception:
                pass

        # Bayesian signals
        try:
            self._dispatch_bayesian_signals(simulation_id, targeting)
        except Exception as exc:
            logger.warning('Bayesian signal dispatch failed: %s', exc)
            try:
                db.session.rollback()
            except Exception:
                pass

        return ProspectList(
            prospects=prospects,
            sources_used=list(set(self._sources_log)),
            total_researched=self._total_researched,
            total_verified=len(prospects),
            total_discarded_invalid=self._discarded_count,
            total_from_apollo=self._from_apollo,
            total_from_web=self._from_web,
            total_from_crm=self._from_crm,
            research_duration_seconds=duration,
        )

    # ── Stage 1 — Apollo ──────────────────────────────────────────────────────

    def _search_apollo(self, user_id: str, targeting: TargetingCriteria, count: int) -> list:
        from app.models.integration import UserIntegration
        from app.services.token_crypto import decrypt_token
        from app.services.apollo_client import ApolloClient

        rec = UserIntegration.query.filter_by(user_id=user_id, provider='apollo').first()
        if not rec or not rec.access_token_enc:
            return []

        token  = decrypt_token(rec.access_token_enc)
        client = ApolloClient(token)

        people = client.people_search(
            person_titles=targeting.job_titles[:5],
            person_seniorities=targeting.seniorities,
            organization_num_employees_ranges=targeting.company_sizes,
            person_locations=targeting.geographies,
            per_page=min(count, 50),
        )
        self._apollo_calls += 1

        prospects = []
        for person in people:
            email = person.get('email') or ''
            if not email:
                continue
            org   = person.get('organization') or {}
            p = Prospect(
                first_name=person.get('first_name', ''),
                last_name=person.get('last_name', ''),
                email=email,
                email_source='apollo',
                email_verified=True,
                email_confidence=1.0,
                job_title=person.get('title', ''),
                company_name=org.get('name', ''),
                company_size=str(org.get('estimated_num_employees', '') or ''),
                company_website=org.get('website_url'),
                industry=org.get('industry'),
                linkedin_url=person.get('linkedin_url'),
                city=person.get('city'),
                state=person.get('state'),
                country=person.get('country', 'United States'),
                source='apollo_people_search',
                apollo_person_id=person.get('id'),
                why_this_fits=f'{person.get("title", "")} at {org.get("name", "")} matches targeting criteria',
            )
            prospects.append(p)
        return prospects

    # ── Stage 2 — Web search ──────────────────────────────────────────────────

    def _select_sources(self, tags: list[str]) -> list[str]:
        seen    = set()
        sources = ['company_websites', 'job_boards']  # always included
        for tag in tags:
            for src in EXPERTISE_TO_SOURCES.get(tag, []):
                if src not in seen:
                    seen.add(src)
                    if src not in sources:
                        sources.append(src)
        return sources

    def _search_web(self, targeting: TargetingCriteria, count: int, agent_context: dict) -> list:
        prospects   = []
        sources     = self._select_sources(targeting.expertise_tags)
        seen_keys: set[str] = set()

        for source in sources:
            if len(prospects) >= count:
                break
            queries = self._build_queries(source, targeting, agent_context)
            for query in queries[:3]:
                if len(prospects) >= count:
                    break
                try:
                    results_text = self._claude_web_search(query)
                    self._web_search_calls += 1
                    if results_text:
                        extracted = self._extract_prospects_from_results(
                            results_text, source, targeting
                        )
                        self._extraction_calls += 1
                        for p in extracted:
                            key = f'{p.first_name.lower()}.{p.last_name.lower()}.{p.company_name.lower()}'
                            if key not in seen_keys:
                                seen_keys.add(key)
                                p.source = f'web_{source}'
                                prospects.append(p)
                        if source not in self._sources_log and extracted:
                            self._sources_log.append(source)
                except Exception as exc:
                    logger.warning('Web search query "%s" failed: %s', query, exc)

        return prospects[:count]

    def _build_queries(self, source: str, targeting: TargetingCriteria, agent_context: dict) -> list[str]:
        tags   = targeting.expertise_tags
        titles = targeting.job_titles
        geos   = targeting.geographies
        geo    = geos[0] if geos else 'United States'
        title  = titles[0] if titles else 'executive'

        if source == 'company_websites':
            queries = [
                f'"{title}" site:linkedin.com {targeting.expertise_zone}',
                f'{targeting.expertise_zone} "{title}" contact email',
            ]
            for company in (targeting.target_companies or [])[:3]:
                queries.append(f'{company} team leadership page contact')
            return queries

        if source == 'government_registries':
            industry = targeting.industries[0] if targeting.industries else ''
            return [
                f'SAM.gov {industry} contractors {geo} point of contact',
                f'site:sam.gov {industry} {geo}',
                f'{geo} state business registry {industry} executive director',
            ]

        if source == 'professional_associations':
            assoc = None
            for tag in tags:
                assoc = EXPERTISE_TO_ASSOCIATION.get(tag)
                if assoc:
                    break
            if assoc:
                return [
                    f'{assoc} member directory {title}',
                    f'{assoc} board of directors {geo}',
                    f'site:{assoc.lower().replace(" ", "")}.org leadership',
                ]
            return [f'{tags[0] if tags else "professional"} association member directory {title}']

        if source == 'job_boards':
            return [
                f'"{title}" job opening {geo} {targeting.expertise_zone}',
                f'site:linkedin.com/jobs {title} {geo}',
                f'{targeting.expertise_zone} hiring {title} {geo}',
            ]

        if source == 'conferences':
            industry = targeting.industries[0] if targeting.industries else ''
            return [
                f'{industry} conference 2026 speakers call for proposals',
                f'{targeting.expertise_zone} summit speaker list 2026',
                f'podcast {targeting.expertise_zone} host contact',
            ]

        if source == 'industry_databases':
            db_name = None
            for tag in tags:
                db_name = EXPERTISE_TO_DATABASE.get(tag)
                if db_name:
                    break
            if db_name == 'crunchbase':
                return [f'site:crunchbase.com {targeting.industries[0] if targeting.industries else ""} CEO founder']
            if db_name == 'clinicaltrials':
                return [f'site:clinicaltrials.gov {tags[0] if tags else "clinical"} principal investigator']
            if db_name == 'guidestar':
                return [f'site:candid.org {tags[0] if tags else "nonprofit"} executive director']
            return [f'{targeting.expertise_zone} industry directory executive contact']

        if source == 'community_forums':
            tag = tags[0] if tags else 'consulting'
            return [
                f'site:reddit.com {tag} looking for consultant expert',
                f'site:quora.com hire {tag} professional',
            ]

        return [f'{targeting.expertise_zone} {title} contact information']

    def _claude_web_search(self, query: str) -> str:
        """Execute a web search via Claude's web_search tool. Returns plain text of results."""
        import anthropic
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])

        messages = [{'role': 'user', 'content': (
            f'Search the web for: {query}\n\n'
            'Return all names, job titles, company names, email addresses, '
            'and LinkedIn URLs you find. Be thorough.'
        )}]

        # Tool loop — web_search_20250305 may require 1-2 turns
        for _ in range(3):
            response = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=2000,
                tools=[{
                    'type': 'web_search_20250305',
                    'name': 'web_search',
                    'max_uses': 2,
                }],
                messages=messages,
            )

            if response.stop_reason == 'end_turn':
                return '\n'.join(
                    b.text for b in response.content if hasattr(b, 'text')
                )

            if response.stop_reason == 'tool_use':
                messages.append({'role': 'assistant', 'content': response.content})
                tool_results = [
                    {'type': 'tool_result', 'tool_use_id': b.id, 'content': ''}
                    for b in response.content
                    if hasattr(b, 'type') and b.type == 'tool_use'
                ]
                if tool_results:
                    messages.append({'role': 'user', 'content': tool_results})
                continue
            break

        return ''

    def _extract_prospects_from_results(
        self, results_text: str, source: str, targeting: TargetingCriteria
    ) -> list:
        """Use Haiku to extract structured prospects from raw web results."""
        import anthropic
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])

        prompt = f"""Extract prospect information from this web content.
Find people matching:
  Job titles: {targeting.job_titles[:4]}
  Industries: {targeting.industries[:3]}

For each person found extract:
  first_name, last_name, job_title, company_name,
  email (only if explicitly shown on page),
  linkedin_url (if shown), company_website, city, state, country

Return a JSON array. If no prospects, return [].
Only extract real, named people. Do NOT invent or guess any field not on the page.

Page content:
{results_text[:4000]}"""

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1500,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]

        try:
            items = json.loads(raw)
        except Exception:
            return []

        prospects = []
        for item in (items or []):
            if not item.get('first_name') or not item.get('last_name'):
                continue
            p = Prospect(
                first_name=item.get('first_name', '').strip(),
                last_name=item.get('last_name', '').strip(),
                email=item.get('email', '').strip(),
                email_source='web_direct' if item.get('email') else '',
                job_title=item.get('job_title', '').strip(),
                company_name=item.get('company_name', '').strip(),
                company_website=item.get('company_website', '').strip() or None,
                linkedin_url=item.get('linkedin_url', '').strip() or None,
                city=item.get('city', '').strip() or None,
                state=item.get('state', '').strip() or None,
                country=item.get('country', 'United States'),
                source=f'web_{source}',
                source_url=item.get('source_url'),
                why_this_fits=(
                    f'{item.get("job_title", "")} at {item.get("company_name", "")} '
                    f'found via {source}'
                ),
            )
            prospects.append(p)
        return prospects

    # ── Stage 3 — Email discovery ─────────────────────────────────────────────

    def _detect_domain(self, company_website: Optional[str]) -> Optional[str]:
        """Detect email domain via three methods in order of reliability."""
        if not company_website:
            return None

        # Normalise URL to bare domain
        domain = re.sub(r'^https?://(www\.)?', '', company_website.rstrip('/'))
        domain = domain.split('/')[0]
        if not domain or '.' not in domain:
            return None

        # Method 1: Hunter.io domain search
        from app.models.platform_settings import PlatformSetting
        hunter_key = PlatformSetting.get('email_verifier_api_key', '')
        provider   = PlatformSetting.get('email_verifier_provider', 'hunter')

        if provider == 'hunter' and hunter_key:
            try:
                import requests as _req
                r = _req.get(
                    'https://api.hunter.io/v2/domain-search',
                    params={'domain': domain, 'api_key': hunter_key, 'limit': 1},
                    timeout=8,
                )
                if r.status_code == 200:
                    data = r.json().get('data', {})
                    if data.get('domain'):
                        return data['domain']
            except Exception:
                pass

        # Method 2: Scrape company contact page for email addresses
        try:
            import requests as _req
            for path in ['/contact', '/about', '/team']:
                try:
                    r = _req.get(f'https://{domain}{path}', timeout=5, allow_redirects=True)
                    if r.status_code == 200:
                        emails = re.findall(r'[\w.+-]+@([\w-]+\.[\w.-]+)', r.text)
                        if emails:
                            return emails[0]
                except Exception:
                    continue
        except Exception:
            pass

        # Method 3: Return the web domain as email domain (best-effort)
        return domain

    def _discover_email(self, prospect: Prospect) -> list[str]:
        """Construct candidate email addresses using known B2B patterns."""
        domain = self._detect_domain(prospect.company_website)
        if not domain:
            return []

        first = re.sub(r'[^a-z]', '', (prospect.first_name or '').lower())
        last  = re.sub(r'[^a-z]', '', (prospect.last_name  or '').lower())
        if not first or not last:
            return []

        f = first[0]
        candidates = []
        for pattern in EMAIL_PATTERNS:
            try:
                candidates.append(pattern.format(first=first, last=last, f=f, domain=domain))
            except Exception:
                pass
        return candidates

    # ── Stage 4 — Email verification ─────────────────────────────────────────

    def _verify_emails(self, prospects: list, skip_verification: bool = False) -> list:
        provider, api_key = _get_verifier_config()
        verified          = []

        for prospect in prospects:
            # Apollo-sourced: already verified
            if prospect.email_verified:
                verified.append(prospect)
                continue

            # Direct email from web page: verify it
            emails_to_check = []
            if prospect.email:
                emails_to_check = [prospect.email]
            elif prospect.email_candidates:
                emails_to_check = prospect.email_candidates

            if not emails_to_check:
                self._discarded_count += 1
                self._save_no_email_contact(prospect)
                continue

            if skip_verification:
                # Budget exhausted — use email as-is without verifying
                prospect.email_source = 'unverified'
                prospect.email_verified = False
                verified.append(prospect)
                continue

            found = False
            for candidate in emails_to_check:
                result = _verify_one_email(candidate, provider, api_key)
                self._verification_cents += 1  # ~$0.01 per call (Hunter rate)

                if result.status == 'valid':
                    prospect.email           = candidate
                    prospect.email_verified  = True
                    prospect.email_source    = 'pattern_verified'
                    prospect.email_confidence = result.confidence
                    verified.append(prospect)
                    found = True
                    break
                if result.status == 'risky':
                    prospect.email           = candidate
                    prospect.email_verified  = True
                    prospect.email_source    = 'pattern_risky'
                    prospect.email_confidence = result.confidence
                    prospect.email_risk_note  = result.risk_reason
                    verified.append(prospect)
                    self._risky_count += 1
                    found = True
                    break
                if result.status == 'unknown':
                    # Retry once then skip
                    retry = _verify_one_email(candidate, provider, api_key)
                    self._verification_cents += 1
                    if retry.status in ('valid', 'risky'):
                        prospect.email          = candidate
                        prospect.email_verified = True
                        prospect.email_source   = f'pattern_{retry.status}'
                        prospect.email_confidence = retry.confidence
                        if retry.status == 'risky':
                            prospect.email_risk_note = retry.risk_reason
                            self._risky_count += 1
                        verified.append(prospect)
                        found = True
                        break
                # 'invalid' → try next candidate

            if not found:
                self._discarded_count += 1
                self._save_no_email_contact(prospect)

        return verified

    def _save_no_email_contact(self, prospect: Prospect):
        """Persist unverifiable prospects to CRM without email for manual follow-up."""
        try:
            from app.models.contact import Contact
            # Only save if we have enough to identify them
            if not prospect.first_name or not prospect.company_name:
                return
            # Avoid duplicates: skip if a contact with same name+company already exists
            existing = Contact.query.filter_by(
                first_name=prospect.first_name,
                last_name=prospect.last_name,
                company_name=prospect.company_name,
            ).first()
            if existing:
                return
            contact = Contact(
                id=generate_id(),
                user_id='_system',   # will be corrected when merged
                first_name=prospect.first_name,
                last_name=prospect.last_name,
                email=f'__no_email_{generate_id()}@placeholder',
                job_title=prospect.job_title,
                company_name=prospect.company_name,
                source='web_research_no_email',
                source_notes=prospect.why_this_fits,
            )
            db.session.add(contact)
        except Exception as exc:
            logger.debug('Could not save no-email contact: %s', exc)

    # ── Stage 5 — CRM merge ───────────────────────────────────────────────────

    def _merge_with_crm(
        self,
        user_id: str,
        simulation_id: str,
        action_id: str,
        prospects: list,
        targeting: TargetingCriteria,
    ) -> list:
        from app.services.contact_lookup import get_contacts_for_action, record_agent_contacts
        from app.models.contact import Contact

        crm_result   = get_contacts_for_action(targeting.agent_type, user_id, simulation_id)
        db_contacts  = crm_result.get('db_contacts', [])
        crm_by_email = {(c.email or '').lower(): c for c in db_contacts if c.email}

        merged            = []
        research_emails   = set()
        new_contact_dicts = []

        for p in prospects:
            email_key = (p.email or '').lower()
            research_emails.add(email_key)

            if email_key and email_key in crm_by_email:
                # CRM record wins; enrich NULL fields
                crm_c = crm_by_email[email_key]
                if not crm_c.linkedin_url and p.linkedin_url:
                    crm_c.linkedin_url = p.linkedin_url
                p.is_existing_crm_contact = True
                p.crm_contact_id          = crm_c.id
                p.crm_pipeline_stage      = crm_c.pipeline_stage
            else:
                # New prospect — queue for CRM upsert
                new_contact_dicts.append(p.to_contact_dict())

            merged.append(p)

        # Upsert new contacts into CRM
        if new_contact_dicts:
            try:
                record_agent_contacts(
                    new_contact_dicts, user_id, simulation_id, action_id, targeting.agent_type,
                )
            except Exception as exc:
                logger.warning('record_agent_contacts failed: %s', exc)

        # Append qualifying CRM contacts NOT found by research
        for c in db_contacts:
            if (c.email or '').lower() not in research_emails:
                crm_p = Prospect(
                    first_name=c.first_name,
                    last_name=c.last_name,
                    email=c.email or '',
                    email_source='crm',
                    email_verified=True,
                    email_confidence=1.0,
                    job_title=c.job_title or '',
                    company_name=c.company_name or '',
                    company_size=c.company_size,
                    industry=c.industry,
                    linkedin_url=c.linkedin_url,
                    source='existing_crm',
                    why_this_fits='Existing qualifying CRM contact',
                    is_existing_crm_contact=True,
                    crm_contact_id=c.id,
                    crm_pipeline_stage=c.pipeline_stage,
                )
                merged.append(crm_p)
                self._from_crm += 1

        return merged

    # ── Logging + Bayesian ────────────────────────────────────────────────────

    def _log_run(
        self,
        user_id: str,
        simulation_id: str,
        action_id: str,
        targeting: TargetingCriteria,
        duration: float,
    ):
        if not user_id:
            try:
                from app.models.simulation import Simulation
                sim = Simulation.query.get(simulation_id)
                user_id = sim.user_id if sim else None
            except Exception:
                pass
        if not user_id:
            return
        run = ProspectResearchRun(
            id=generate_id(),
            simulation_id=simulation_id,
            user_id=user_id,
            action_id=action_id,
            calling_agent=targeting.agent_type,
            targeting_criteria=json.dumps({
                'expertise_zone':  targeting.expertise_zone,
                'expertise_tags':  targeting.expertise_tags,
                'job_titles':      targeting.job_titles,
                'seniorities':     targeting.seniorities,
                'company_sizes':   targeting.company_sizes,
                'industries':      targeting.industries,
                'geographies':     targeting.geographies,
                'pain_point':      targeting.pain_point,
            }),
            sources_used=json.dumps(self._sources_log),
            total_researched=self._total_researched,
            total_from_apollo=self._from_apollo,
            total_from_web=self._from_web,
            total_from_crm=self._from_crm,
            total_verified=self._total_researched - self._discarded_count,
            total_discarded_invalid=self._discarded_count,
            total_risky=self._risky_count,
            verification_cost_cents=self._verification_cents,
            apollo_api_calls=self._apollo_calls,
            web_search_calls=self._web_search_calls,
            extraction_calls=self._extraction_calls,
            duration_seconds=duration,
        )
        db.session.add(run)
        db.session.commit()

    def _dispatch_bayesian_signals(self, simulation_id: str, targeting: TargetingCriteria):
        from app.services.bayesian_service import dispatch_signal

        total = max(self._total_researched, 1)

        # research_coverage_rate: how full was the prospect list vs target (25)
        coverage = min((total - self._discarded_count) / 25.0, 1.0)
        dispatch_signal(simulation_id, 'research_coverage_rate', coverage, 0.2, '+')

        # email_validity_rate per source
        valid_count = total - self._discarded_count
        validity    = valid_count / total
        for src in self._sources_log:
            dispatch_signal(
                simulation_id, f'email_validity_rate:{src}', validity, 0.3,
                '+' if validity >= 0.5 else '-',
            )

        # verification_cost_efficiency: cost per verified prospect
        if valid_count > 0:
            cost_per = self._verification_cents / valid_count
            # Lower cost is better — signal is inverse
            efficiency = max(0.0, 1.0 - (cost_per / 100.0))
            dispatch_signal(simulation_id, 'verification_cost_efficiency', efficiency, 0.2, '+')

        # source_freshness: each source used now gets a freshness signal
        for src in self._sources_log:
            dispatch_signal(simulation_id, f'source_freshness:{src}', 1.0, 0.3, '+')

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _has_integration(user_id: str, provider: str) -> bool:
        from app.models.integration import UserIntegration
        rec = UserIntegration.query.filter_by(user_id=user_id, provider=provider).first()
        return bool(rec and rec.access_token_enc)
