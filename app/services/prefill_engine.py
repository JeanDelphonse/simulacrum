"""Pre-fill Engine — generates confidence-scored form field values before any agent action.

Priority chain (highest wins):
  1. Bayesian correction model  (prefill_corrections, 3+ same corrections → High override)
  2. Upstream artifact dependency (fields carried from prior action outputs)
  3. Prior action outputs         (AgentContext / completed AgentActions)
  4. Expertise Zone + deliverables
  5. Resume / LinkedIn parsed_text heuristics
  6. Platform defaults + heuristics
"""
from __future__ import annotations
from typing import Optional
import json
import logging
import re
from collections import Counter
from datetime import datetime

logger = logging.getLogger(__name__)

CONFIDENCE_HIGH   = 'high'
CONFIDENCE_MEDIUM = 'medium'
CONFIDENCE_LOW    = 'low'

BAYESIAN_PROMOTION_THRESHOLD = 3


class PrefillField:
    def __init__(self, key: str, value: str, confidence: str, source: str, tooltip: str):
        self.key        = key
        self.value      = value
        self.confidence = confidence
        self.source     = source
        self.tooltip    = tooltip

    def to_dict(self) -> dict:
        return {
            'key':        self.key,
            'value':      self.value,
            'confidence': self.confidence,
            'source':     self.source,
            'tooltip':    self.tooltip,
        }


# ---------------------------------------------------------------------------
# Per-field rules:  field_key → (value_fn, confidence, source, tooltip)
# value_fn receives the PrefillEngine instance and returns Optional[str]
# A None return means "no value from this source — fall through"
# ---------------------------------------------------------------------------

def _zone(e):     return e._expertise_zone
def _rate(e):     return e._zone_rate()
def _summary(e):  return e._zone_summary()
def _deliv(e):    return e._zone_deliverables_str()
def _company(e):  return e._resume_company_size()
def _geo(e):      return e._resume_geography()


# (value_fn, confidence, source, tooltip_template)
# tooltip_template may contain {val} which is substituted at render time
_FIELD_RULES: dict[str, tuple] = {

    # ── Layer 1 ──────────────────────────────────────────────────────────────
    'target_industries': (
        _zone, CONFIDENCE_HIGH, 'expertise_zone',
        'Pre-filled from your Expertise Zone: {val}.',
    ),
    'target_industry': (
        _zone, CONFIDENCE_HIGH, 'expertise_zone',
        'Pre-filled from your Expertise Zone: {val}.',
    ),
    'engagement_model': (
        lambda e: 'retainer', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — retainer is the most common model for repeat consulting work.',
    ),
    'tone': (
        lambda e: 'professional', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — professional tone is the safest starting point.',
    ),
    'target_company_size': (
        _company, CONFIDENCE_HIGH, 'resume',
        'Inferred from your resume employer history: {val}.',
    ),
    'target_company_sizes': (
        _company, CONFIDENCE_HIGH, 'resume',
        'Inferred from your resume employer history: {val}.',
    ),
    'pain_point': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Based on your Expertise Zone summary — adjust to the specific pain point you solve.',
    ),
    'daily_send_limit': (
        lambda e: '30', CONFIDENCE_LOW, 'platform_default',
        'Apollo / Instantly best practice for new sender domains: 30 emails/day.',
    ),
    'target_rate': (
        _rate, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Pre-filled from your Expertise Zone market rate estimate: {val}.',
    ),
    'engagement_preference': (
        lambda e: 'fixed-project', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — fixed-project engagements are easiest to scope and price.',
    ),
    'client_outcomes': (
        _deliv, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Derived from your Expertise Zone deliverables — refine with your top 3 client outcomes.',
    ),
    'location_preference': (
        lambda e: 'remote', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — remote is the default for most fractional and consulting roles.',
    ),
    'minimum_monthly_rate': (
        lambda e: e._min_monthly_rate(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Estimated from your Expertise Zone hourly rate × 40 hours / month.',
    ),
    'industries_to_exclude': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — no exclusions assumed. Fill in if you have restrictions.',
    ),
    'open_to_engagements': (
        lambda e: 'yes', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — defaulting to open. Update if you are at capacity.',
    ),
    'profile_focus': (
        lambda e: 'consulting availability', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — consulting availability is the most common profile focus for active income.',
    ),
    'meeting_types': (
        lambda e: '30-minute intro call, 60-minute consulting session', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — standard intro + working session pair. Adjust durations to match your rate.',
    ),
    'available_days': (
        lambda e: 'Monday–Friday, 9am–5pm', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — standard business hours. Adjust to your actual availability.',
    ),
    'paid_session_rate': (
        _rate, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Pre-filled from your Expertise Zone market rate estimate: {val}.',
    ),
    'client_name': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Client-specific — agent cannot pre-fill. Enter the prospect\'s name.',
    ),
    'project_scope': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone summary — replace with this specific client\'s scope.',
    ),
    'payment_milestones': (
        lambda e: '50% upfront / 50% delivery', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 50/50 split is the standard for consulting engagements.',
    ),
    'payment_terms': (
        lambda e: 'net-30', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — net-30 is the most common consulting payment term.',
    ),
    'clauses': (
        lambda e: 'IP ownership, confidentiality, non-solicitation', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — standard clauses for most consulting agreements.',
    ),
    'top_referrers': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Relationship-specific — agent cannot pre-fill. Enter your top 3–5 contacts.',
    ),
    'referral_type': (
        _zone, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Based on your Expertise Zone: {val} — specify the type of engagement to refer.',
    ),
    'referral_incentive': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Optional — leave blank or add a thank-you gift or referral commission.',
    ),
    'existing_clients': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Relationship-specific — list clients or colleagues who have praised your work.',
    ),
    'display_platform': (
        lambda e: 'LinkedIn', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — LinkedIn is the highest-impact platform for B2B social proof.',
    ),
    'format': (
        lambda e: 'short quotes', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — short quotes are easiest to collect and display.',
    ),
    'offered_rate': (
        _rate, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Pre-filled from your Expertise Zone market rate: {val}. Adjust to the specific offer.',
    ),
    'client_size': (
        _company, CONFIDENCE_HIGH, 'resume',
        'Inferred from your resume employer history: {val}.',
    ),
    'walkaway_rate': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter the minimum rate you would accept.',
    ),
    'audience': (
        _zone, CONFIDENCE_HIGH, 'expertise_zone',
        'Pre-filled from your Expertise Zone: {val}.',
    ),
    'key_message': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone summary — refine to a single compelling thesis.',
    ),

    # ── Layer 2 ──────────────────────────────────────────────────────────────
    'event_type': (
        lambda e: 'all', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — casting wide initially surfaces the best-fit opportunities.',
    ),
    'talk_format': (
        lambda e: 'breakout (20 min)', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — breakout sessions are easiest to land for first-time speakers.',
    ),
    'geographic_regions': (
        _geo, CONFIDENCE_MEDIUM, 'resume',
        'Inferred from your resume location references: {val}.',
    ),
    'speaking_stage': (
        lambda e: 'building visibility (free)', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — building visibility first maximises inbound opportunities.',
    ),
    'keynote_fee': (
        lambda e: e._keynote_fee(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Estimated from your consulting rate — keynote fees typically run 2–4× your day rate.',
    ),
    'logistical_requirements': (
        lambda e: 'Travel and hotel required; AV: projector and lapel mic', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — standard speaker rider items. Adjust to your preferences.',
    ),
    'duration_weeks': (
        lambda e: '8', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 8 weeks is the sweet spot for group coaching transformation arcs.',
    ),
    'transformation_goal': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone summary — refine to a specific participant transformation.',
    ),
    'cohort_size': (
        lambda e: '12', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 12 participants balances income and intimacy for first cohorts.',
    ),
    'price_per_participant': (
        lambda e: e._cohort_price(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Estimated from your consulting rate and programme duration: {val}.',
    ),
    'buyer_departments': (
        lambda e: 'L&D, HR, Operations', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — L&D and HR are the primary buyers for corporate training.',
    ),
    'workshop_duration': (
        lambda e: 'full-day', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — full-day is the standard unit for corporate training engagements.',
    ),
    'participant_outcome': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone — specify the single skill participants leave with.',
    ),
    'participant_level': (
        lambda e: 'intermediate', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — intermediate audiences are the largest addressable segment.',
    ),
    'program_name': (
        lambda e: e._program_name(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone: {val} — refine to a compelling programme name.',
    ),
    'start_date_price': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Enter your anticipated launch date and price for this cohort.',
    ),
    'application_type': (
        lambda e: 'full application (name, role, company, goals)', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — full applications help you qualify participants and personalise the cohort.',
    ),
    'multiple_cohorts': (
        lambda e: 'yes', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — planning for multiple cohorts from the start shapes a better curriculum.',
    ),
    'alumni_offer': (
        lambda e: 'advanced cohort', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — an advanced cohort is the highest-value alumni offer.',
    ),
    'prep_hours': (
        lambda e: '20', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 20 hours is typical for a first full-day workshop.',
    ),
    'current_rate': (
        _rate, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Pre-filled from your Expertise Zone market rate estimate: {val}.',
    ),
    'quarterly_capacity': (
        lambda e: '2', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 2 workshops or cohorts per quarter is sustainable alongside other layers.',
    ),

    # ── Layer 3 ──────────────────────────────────────────────────────────────
    'course_title': (
        lambda e: e._course_title(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone: {val} — refine to a student-facing title.',
    ),
    'target_student': (
        lambda e: e._target_student(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone audience signals.',
    ),
    'hosting_platform': (
        lambda e: 'Maven', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — Maven is the leading platform for cohort-based expert courses.',
    ),
    'price_point': (
        lambda e: e._course_price(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Estimated from your consulting rate — self-paced courses typically price at 10–20× hourly rate.',
    ),
    'competitor_courses': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Research required — enter 2–3 courses you consider comparable.',
    ),
    'differentiation': (
        lambda e: 'methodology', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — methodology differentiation is the most defensible for domain experts.',
    ),
    'product_name': (
        lambda e: e._course_title(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone: {val}.',
    ),
    'transformation': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone summary — sharpen to a single before/after transformation.',
    ),
    'price': (
        lambda e: e._course_price(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Estimated from your Expertise Zone market rate.',
    ),
    'early_testimonials': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Optional — paste any beta feedback or early student quotes here.',
    ),
    'guide_problem': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone summary — refine to the specific problem your guide solves.',
    ),
    'guide_depth': (
        lambda e: 'definitive guide (15,000+ words)', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — a definitive guide commands a higher price and stronger SEO value.',
    ),
    'guide_type': (
        lambda e: 'paid product', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — paid products generate direct revenue; use free if building a list.',
    ),
    'current_price': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Enter the current product price to test against alternatives.',
    ),
    'alternative_prices': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Enter 2–3 alternative price points to test.',
    ),
    'conversion_metric': (
        lambda e: 'checkout page conversions', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — checkout conversions are the clearest A/B success metric.',
    ),
    'platform': (
        lambda e: 'Circle', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — Circle is the leading platform for expert membership communities.',
    ),
    'monthly_price': (
        lambda e: '$97', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — $97/month is a proven price point for professional communities.',
    ),
    'community_name': (
        lambda e: e._community_name(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone: {val}.',
    ),
    'product_name_price': (
        lambda e: e._product_name_price(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone — refine to the specific product and its price.',
    ),
    'days_before_launch': (
        lambda e: '14', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 14-day launch sequences are the sweet spot for creator products.',
    ),
    'early_bird': (
        lambda e: '20% off — first 48 hours only', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 48-hour early-bird creates urgency without devaluing long-term pricing.',
    ),
    'commission_rate': (
        lambda e: '30%', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 30% is the standard affiliate commission for digital products.',
    ),
    'commission_type': (
        lambda e: 'one-time', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — one-time commission is standard for course and product affiliates.',
    ),
    'program_type': (
        lambda e: 'curated (invite-only)', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — curated programmes attract higher-quality affiliates.',
    ),
    'student_count': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Enter your current student or customer count.',
    ),
    'testimonial_format': (
        lambda e: 'short quotes', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — short quotes are fastest to collect and most versatile to display.',
    ),
    'request_timing': (
        lambda e: 'at completion', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — completion is peak satisfaction; the best moment to request a testimonial.',
    ),
    'lapsed_threshold': (
        lambda e: '60 days', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 60 days of inactivity is the standard reactivation trigger.',
    ),
    'reengagement_offer': (
        lambda e: 'bonus module', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — a bonus module adds value without discounting.',
    ),
    'pack_focus': (
        _zone, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Pre-filled from your Expertise Zone: {val}.',
    ),
    'target_buyer': (
        lambda e: e._target_student(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone audience signals.',
    ),

    # ── Layer 4 ──────────────────────────────────────────────────────────────
    'content_domain': (
        _zone, CONFIDENCE_HIGH, 'expertise_zone',
        'Pre-filled from your Expertise Zone: {val}.',
    ),
    'target_reader': (
        lambda e: e._target_reader(), CONFIDENCE_MEDIUM, 'resume',
        'Inferred from your resume employer profile: {val}.',
    ),
    'publishing_frequency': (
        lambda e: '2x per week', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 2× per week is the recommended cadence for new SEO content sites.',
    ),
    'lead_magnet_type': (
        lambda e: 'checklist', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — checklists are the fastest to produce and highest-converting lead magnets.',
    ),
    'funnel_goal': (
        lambda e: e._funnel_goal(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Based on your Expertise Zone — the funnel should lead toward: {val}.',
    ),
    'email_platform': (
        lambda e: 'ConvertKit', CONFIDENCE_LOW, 'platform_default',
        'Platform default — ConvertKit is the standard for creator-led email lists.',
    ),
    'list_size': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Enter your current email list size.',
    ),
    'open_rate': (
        lambda e: '30', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 30% is a strong open rate for engaged professional lists.',
    ),
    'monetization_model': (
        lambda e: 'sponsorships', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — sponsorships are the fastest path to newsletter monetisation.',
    ),
    'problem_to_solve': (
        _summary, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone summary — refine to the specific problem your SaaS solves.',
    ),
    'target_customer': (
        lambda e: e._target_student(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone audience signals.',
    ),
    'pricing_model': (
        lambda e: 'flat monthly', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — flat monthly pricing is simplest for early-stage micro-SaaS.',
    ),
    'ip_to_license': (
        _deliv, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Drafted from your Expertise Zone deliverables — identify the methodology or framework to license.',
    ),
    'target_licensees': (
        lambda e: 'companies', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — corporate licensees pay the highest fees.',
    ),
    'licensing_model': (
        lambda e: 'annual flat fee', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — annual flat fee is easiest to administer and predict.',
    ),
    'tools_already_recommend': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Optional — list tools or platforms you already recommend to clients.',
    ),
    'affiliate_direction': (
        lambda e: 'both', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — both directions maximise affiliate revenue.',
    ),
    'production_frequency': (
        lambda e: 'weekly', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — weekly is the minimum cadence for algorithmic growth.',
    ),
    'end_cta': (
        lambda e: 'consultation', CONFIDENCE_MEDIUM, 'expertise_zone',
        'Based on your Expertise Zone — a consultation CTA drives the highest-value leads.',
    ),
    'audience_size': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Enter your combined audience size across all platforms.',
    ),
    'highest_engagement_platform': (
        lambda e: 'LinkedIn', CONFIDENCE_MEDIUM, 'resume',
        'Agent estimate — LinkedIn is the primary high-engagement platform for B2B professionals.',
    ),
    'follow_reason': (
        lambda e: 'education', CONFIDENCE_MEDIUM, 'expertise_zone',
        'Agent estimate — professional domain experts attract followers for educational content.',
    ),
    'product_to_advertise': (
        lambda e: e._course_title(), CONFIDENCE_MEDIUM, 'expertise_zone',
        'Pre-filled from your Expertise Zone product name: {val}.',
    ),
    'monthly_ad_budget': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Enter your monthly ad budget in USD.',
    ),
    'ad_platforms': (
        lambda e: e._ad_platforms(), CONFIDENCE_MEDIUM, 'resume',
        'Recommended based on your target audience profile: {val}.',
    ),
    'estimated_lapsed_clients': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Optional — estimate how many lapsed consulting or coaching clients you have.',
    ),

    # ── Layer 5 ──────────────────────────────────────────────────────────────
    'monthly_income': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter your estimated monthly income from Layers 1–4.',
    ),
    'monthly_expenses': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter your fixed monthly expenses.',
    ),
    'risk_tolerance': (
        lambda e: 'moderate', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — moderate is the most common starting risk profile. Adjust to your preference.',
    ),
    'investment_horizon': (
        lambda e: '10', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 10-year horizon is a common planning window for wealth deployment.',
    ),
    'monthly_deploy': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter the amount you can deploy into investments each month.',
    ),
    'years_to_model': (
        lambda e: '20', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 20-year model shows the full compound growth curve.',
    ),
    'return_assumption': (
        lambda e: 'moderate (8%)', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 8% is the historical average for diversified global equity portfolios.',
    ),
    'diversification': (
        lambda e: 'global', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — global diversification reduces single-market risk.',
    ),
    'esg_preference': (
        lambda e: "don't mind", CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — no preference assumed. Adjust if ESG matters to you.',
    ),
    'account_type': (
        lambda e: 'taxable account', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — taxable account is the default starting point. Adjust for your tax situation.',
    ),
    'financial_goals': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter your top three financial goals and target dates.',
    ),
    'min_return': (
        lambda e: '7', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 7% is a reasonable minimum for a diversified long-term portfolio.',
    ),
    'rebalance_triggers': (
        lambda e: '5% drift from target allocation, or annual review', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — 5% drift trigger is the standard rebalancing rule.',
    ),
    'investable_capital': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter the capital you have available for real estate investment.',
    ),
    'investment_vehicle': (
        lambda e: 'compare all', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — comparing all options first ensures the best fit for your situation.',
    ),
    'hold_period': (
        lambda e: 'long-term appreciation', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — long-term appreciation aligns best with a wealth-building strategy.',
    ),
    'business_entity': (
        lambda e: 'LLC', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — LLC is the most common starting entity for consulting businesses.',
    ),
    'annual_gross_income': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter your estimated annual gross income from all layers.',
    ),
    'has_retirement_account': (
        lambda e: 'none', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — no retirement account assumed. Update if you have one.',
    ),
    'current_entity': (
        lambda e: 'LLC', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — LLC is the most common entity for consulting businesses.',
    ),
    'annual_revenue': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter your current annual revenue from all sources.',
    ),
    'raise_investment': (
        lambda e: 'no', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — no outside investment assumed. Adjust if you plan to raise.',
    ),
    'brokerage': (
        lambda e: 'Fidelity', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — Fidelity has the lowest friction for DCA automation.',
    ),
    'contribution_amount': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter the fixed amount you will contribute each cycle.',
    ),
    'contribution_frequency': (
        lambda e: 'monthly', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — monthly DCA aligns with income payment cycles.',
    ),
    'current_coverage': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — list your current insurance coverage.',
    ),
    'annual_income': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Personal — enter the annual income you would need to replace if unable to work.',
    ),
    'l1_consulting_volume': (
        lambda e: None, CONFIDENCE_LOW, 'platform_default',
        'Optional — enter your estimated annual Layer 1 consulting revenue.',
    ),
    'has_will': (
        lambda e: 'no', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — no will assumed. Update to reflect your situation.',
    ),
    'has_dependents': (
        lambda e: 'no', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — no dependents assumed. Update if you have a spouse or children.',
    ),
    'business_assets': (
        lambda e: 'no', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — no business assets assumed. Update if you have IP or equity.',
    ),
    'industries_to_invest': (
        _zone, CONFIDENCE_MEDIUM, 'expertise_zone',
        'Based on your Expertise Zone: {val} — investing in your domain gives you an informational edge.',
    ),
    'angel_investing_interest': (
        lambda e: 'maybe', CONFIDENCE_LOW, 'platform_default',
        'Agent estimate — "maybe" keeps options open until you review your liquidity.',
    ),
}


class PrefillEngine:
    """Generates a prefill payload for a given action type in a simulation."""

    def __init__(self, simulation, resume, action_type: str, layer_number: int):
        self.simulation   = simulation
        self.resume       = resume
        self.action_type  = action_type
        self.layer_number = layer_number
        self._parsed_text = (resume.parsed_text or '') if resume else ''
        self._expertise_zone = simulation.expertise_zone or ''
        self._zone_data   = self._load_zone_data()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def generate(self) -> dict[str, dict]:
        from app.services.claude import AGENT_ACTION_TYPES
        action_meta = AGENT_ACTION_TYPES.get(self.layer_number, {}).get(self.action_type)
        if not action_meta:
            return {}
        result = {}
        for field in action_meta.get('prompt_form', []):
            key = field['key']
            result[key] = self._resolve_field(key).to_dict()
        return result

    # -------------------------------------------------------------------------
    # Priority chain
    # -------------------------------------------------------------------------

    def _resolve_field(self, key: str) -> PrefillField:
        # 1. Bayesian correction model
        pf = self._from_bayesian(key)
        if pf:
            return pf

        # 2. Upstream artifact dependency
        pf = self._from_upstream_artifact(key)
        if pf:
            return pf

        # 3. Prior AgentContext for this simulation/layer
        pf = self._from_prior_context(key)
        if pf:
            return pf

        # 4+5+6. Field rules (zone / resume / default)
        return self._from_field_rules(key)

    # -------------------------------------------------------------------------
    # Source 1: Bayesian correction model
    # -------------------------------------------------------------------------

    def _from_bayesian(self, key: str) -> Optional[PrefillField]:
        try:
            from app.models.artifact import PrefillCorrection
            corrections = PrefillCorrection.query.filter_by(
                simulation_id=self.simulation.id,
                action_type=self.action_type,
                field_name=key,
            ).order_by(PrefillCorrection.created_at.desc()).all()

            if not corrections:
                return None

            values = [c.corrected_value for c in corrections if c.corrected_value]
            if not values:
                return None

            most_common, count = Counter(values).most_common(1)[0]

            if count >= BAYESIAN_PROMOTION_THRESHOLD:
                confidence = CONFIDENCE_HIGH
                tooltip = (
                    f'Learned from your {count} previous responses — you consistently '
                    f'use this value for this field.'
                )
            else:
                confidence = CONFIDENCE_MEDIUM
                tooltip = (
                    f'Based on your last {len(corrections)} response(s) for this action type. '
                    f'You previously entered this value.'
                )

            return PrefillField(key=key, value=most_common, confidence=confidence,
                                source='bayesian_correction', tooltip=tooltip)
        except Exception as e:
            logger.debug('Bayesian prefill error %s/%s: %s', self.action_type, key, e)
        return None

    # -------------------------------------------------------------------------
    # Source 2: Upstream artifact dependency
    # -------------------------------------------------------------------------

    def _from_upstream_artifact(self, key: str) -> Optional[PrefillField]:
        try:
            from app.models.artifact import ArtifactDependency, ArtifactVersion
            from app.models.agent_action import AgentAction

            deps = ArtifactDependency.query.filter_by(
                simulation_id=self.simulation.id,
                downstream_action_type=self.action_type,
            ).all()

            for dep in deps:
                if key not in dep.fields_passed:
                    continue
                if not dep.upstream_action_id:
                    continue
                version = ArtifactVersion.query.filter_by(
                    action_id=dep.upstream_action_id,
                    is_current=True,
                ).first()
                if not version:
                    continue
                value = version.prefill_inputs.get(key)
                if value:
                    upstream = AgentAction.query.get(dep.upstream_action_id)
                    label = upstream.action_type.replace('_', ' ').title() if upstream else dep.upstream_action_type
                    return PrefillField(
                        key=key, value=str(value),
                        confidence=CONFIDENCE_HIGH,
                        source='upstream_artifact',
                        tooltip=f'Carried forward from your {label} artifact (Layer {dep.upstream_action_id}).',
                    )
        except Exception as e:
            logger.debug('Upstream artifact prefill error %s/%s: %s', self.action_type, key, e)
        return None

    # -------------------------------------------------------------------------
    # Source 3: Prior AgentContext
    # -------------------------------------------------------------------------

    def _from_prior_context(self, key: str) -> Optional[PrefillField]:
        try:
            from app.models.agent_context import AgentContext
            ctx = AgentContext.get_for_layer(self.simulation.id, self.layer_number)
            value = ctx.get(key)
            if value:
                return PrefillField(
                    key=key, value=str(value),
                    confidence=CONFIDENCE_MEDIUM,
                    source='prior_action_output',
                    tooltip='Based on your previous response for this field in this layer.',
                )
        except Exception as e:
            logger.debug('Prior context prefill error %s/%s: %s', self.action_type, key, e)
        return None

    # -------------------------------------------------------------------------
    # Source 4-6: Field rules map
    # -------------------------------------------------------------------------

    def _from_field_rules(self, key: str) -> PrefillField:
        rule = _FIELD_RULES.get(key)
        if rule:
            value_fn, confidence, source, tooltip_tmpl = rule
            try:
                value = value_fn(self)
            except Exception as e:
                logger.debug('Field rule error for %s: %s', key, e)
                value = None

            if value is None:
                # Return low-confidence blank for explicitly unmappable fields
                return PrefillField(
                    key=key, value='',
                    confidence=CONFIDENCE_LOW,
                    source=source,
                    tooltip=tooltip_tmpl.replace('{val}', ''),
                )

            tooltip = tooltip_tmpl.replace('{val}', str(value))
            return PrefillField(key=key, value=str(value), confidence=confidence,
                                source=source, tooltip=tooltip)

        # Truly unknown field key — return blank low
        return PrefillField(
            key=key, value='',
            confidence=CONFIDENCE_LOW,
            source='platform_default',
            tooltip='Agent estimate — no data available for this field. Please fill in.',
        )

    # -------------------------------------------------------------------------
    # Zone / resume helper methods
    # -------------------------------------------------------------------------

    def _load_zone_data(self) -> dict:
        """Return the first matching expertise zone dict from resume data."""
        if not self.resume:
            return {}
        try:
            zones = self.resume.expertise_zones or []
            zone_name_lower = self._expertise_zone.lower()
            for z in zones:
                if isinstance(z, dict) and zone_name_lower in z.get('zone_name', '').lower():
                    return z
            return zones[0] if zones else {}
        except Exception:
            return {}

    def _zone_rate(self) -> Optional[str]:
        rate = self._zone_data.get('estimated_hourly_rate')
        if rate:
            return str(rate)
        # Fallback: scan parsed_text for a rate pattern
        if self._parsed_text:
            m = re.search(r'\$(\d{2,4})\s*[-–/]\s*\$?(\d{2,4})\s*/?\s*hr', self._parsed_text, re.IGNORECASE)
            if m:
                return f'${m.group(1)}–${m.group(2)}/hr'
        return None

    def _zone_summary(self) -> Optional[str]:
        return self._zone_data.get('summary') or (self._expertise_zone or None)

    def _zone_deliverables_str(self) -> Optional[str]:
        deliverables = self._zone_data.get('deliverables', [])
        if deliverables and isinstance(deliverables, list):
            return '; '.join(str(d) for d in deliverables[:3])
        return self._expertise_zone or None

    def _resume_company_size(self) -> Optional[str]:
        if not self._parsed_text:
            return None
        text = self._parsed_text.lower()
        enterprise_signals = [
            'fortune 500', 'fortune 50', 'fortune 10', 'enterprise',
            'adobe', 'dell', 'jpmorgan', 'jpmc', 'wells fargo', 'microsoft',
            'google', 'amazon', 'meta', 'apple', 'ibm', 'oracle', 'salesforce',
            'accenture', 'deloitte', 'mckinsey', 'goldman', 'morgan stanley',
        ]
        if any(s in text for s in enterprise_signals):
            return 'Enterprise (5,000+ employees)'
        smb_signals = ['startup', 'small business', 'seed stage', 'series a', 'boutique']
        if any(s in text for s in smb_signals):
            return 'SMB (10–500 employees)'
        return 'mid-market'

    def _resume_geography(self) -> Optional[str]:
        if not self._parsed_text:
            return None
        text = self._parsed_text.lower()
        for hint, region in [
            ('new york', 'North America — East Coast'),
            ('san francisco', 'North America — West Coast'),
            ('los angeles', 'North America — West Coast'),
            ('chicago', 'North America — Midwest'),
            ('toronto', 'North America — Canada'),
            ('london', 'Europe — UK'),
            ('amsterdam', 'Europe — Netherlands'),
            ('singapore', 'Asia Pacific'),
            ('sydney', 'Asia Pacific — Australia'),
        ]:
            if hint in text:
                return region
        return None

    def _min_monthly_rate(self) -> Optional[str]:
        rate = self._zone_rate()
        if not rate:
            return None
        m = re.search(r'\$(\d+)', rate)
        if m:
            hourly_low = int(m.group(1))
            monthly = hourly_low * 40
            return f'${monthly:,}'
        return None

    def _keynote_fee(self) -> Optional[str]:
        rate = self._zone_rate()
        if not rate:
            return None
        m = re.search(r'\$(\d+)', rate)
        if m:
            day_rate = int(m.group(1)) * 8
            keynote = day_rate * 2
            return f'${keynote:,}'
        return None

    def _cohort_price(self) -> Optional[str]:
        rate = self._zone_rate()
        if not rate:
            return None
        m = re.search(r'\$(\d+)', rate)
        if m:
            hourly = int(m.group(1))
            # 8 weeks × 2 hours/week × hourly rate
            price = hourly * 16
            return f'${price:,}'
        return None

    def _course_price(self) -> Optional[str]:
        rate = self._zone_rate()
        if not rate:
            return None
        m = re.search(r'\$(\d+)', rate)
        if m:
            hourly = int(m.group(1))
            # Self-paced course ≈ 15× hourly rate
            price = hourly * 15
            return f'${price:,}'
        return None

    def _course_title(self) -> Optional[str]:
        if not self._expertise_zone:
            return None
        zone = self._expertise_zone
        deliv = self._zone_data.get('deliverables', [])
        if deliv and isinstance(deliv, list) and len(deliv) > 0:
            return f'The {zone} Masterclass'
        return f'{zone}: A Complete Practitioner Course'

    def _community_name(self) -> Optional[str]:
        if not self._expertise_zone:
            return None
        return f'The {self._expertise_zone} Circle'

    def _program_name(self) -> Optional[str]:
        if not self._expertise_zone:
            return None
        return f'{self._expertise_zone} Accelerator'

    def _product_name_price(self) -> Optional[str]:
        title = self._course_title()
        price = self._course_price()
        if title and price:
            return f'{title} — {price}'
        return title

    def _target_student(self) -> Optional[str]:
        zone = self._expertise_zone
        company_size = self._resume_company_size()
        if zone and company_size:
            return f'Mid-to-senior professionals working in {company_size} organisations who want to master {zone}.'
        if zone:
            return f'Professionals who want to develop expertise in {zone}.'
        return None

    def _target_reader(self) -> str:
        company_size = self._resume_company_size()
        if company_size and 'Enterprise' in company_size:
            return 'executive'
        return 'practitioner'

    def _funnel_goal(self) -> Optional[str]:
        title = self._course_title()
        if title:
            return f'Enrolment in {title}'
        return f'Consultation booking — {self._expertise_zone}' if self._expertise_zone else None

    def _ad_platforms(self) -> str:
        company_size = self._resume_company_size()
        if company_size and 'Enterprise' in company_size:
            return 'LinkedIn, Google Search'
        return 'Meta, Google Search'


# ---------------------------------------------------------------------------
# Staleness propagation
# ---------------------------------------------------------------------------

def propagate_staleness(simulation_id: str, upstream_action_id: str, new_version_number: int) -> int:
    """BFS staleness propagation capped at MAX_CASCADE_DEPTH=5."""
    MAX_CASCADE_DEPTH = 5
    try:
        from app.extensions import db
        from app.models.artifact import ArtifactDependency

        visited = set()
        queue = [(upstream_action_id, 0)]
        stale_count = 0

        while queue:
            current_id, depth = queue.pop(0)
            if depth >= MAX_CASCADE_DEPTH or current_id in visited:
                continue
            visited.add(current_id)

            deps = ArtifactDependency.query.filter_by(
                simulation_id=simulation_id,
                upstream_action_id=current_id,
            ).all()

            for dep in deps:
                if dep.upstream_version_used is None or dep.upstream_version_used < new_version_number:
                    dep.is_stale = True
                    dep.stale_detected_at = datetime.utcnow()
                    dep.resolved_at = None
                    stale_count += 1

                    from app.models.agent_action import AgentAction
                    for da in AgentAction.query.filter_by(
                        simulation_id=simulation_id,
                        action_type=dep.downstream_action_type,
                        status=AgentAction.STATUS_COMPLETE,
                    ).all():
                        queue.append((da.id, depth + 1))

        if stale_count:
            db.session.commit()
            _notify_orchestrator(simulation_id, stale_count)

        return stale_count
    except Exception as e:
        logger.error('propagate_staleness failed sim=%s action=%s: %s', simulation_id, upstream_action_id, e)
        return 0


def resolve_staleness(simulation_id: str, upstream_action_id: str, consumed_version: int):
    try:
        from app.extensions import db
        from app.models.artifact import ArtifactDependency
        deps = ArtifactDependency.query.filter_by(
            simulation_id=simulation_id,
            upstream_action_id=upstream_action_id,
            is_stale=True,
        ).all()
        for dep in deps:
            dep.is_stale = False
            dep.upstream_version_used = consumed_version
            dep.resolved_at = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        logger.error('resolve_staleness failed: %s', e)


def seed_artifact_dependencies(simulation_id: str):
    """Populate artifact_dependencies from artifact_dependency_config.json (idempotent)."""
    import os
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'artifact_dependency_config.json',
    )
    try:
        with open(config_path) as f:
            config = json.load(f)
    except Exception as e:
        logger.error('Could not load artifact_dependency_config.json: %s', e)
        return

    try:
        from app.extensions import db
        from app.models.artifact import ArtifactDependency
        from utils.id_gen import generate_id

        for dep_cfg in config.get('dependencies', []):
            exists = ArtifactDependency.query.filter_by(
                simulation_id=simulation_id,
                upstream_action_type=dep_cfg['upstream_action_type'],
                downstream_action_type=dep_cfg['downstream_action_type'],
            ).first()
            if not exists:
                dep = ArtifactDependency(
                    id=generate_id(),
                    simulation_id=simulation_id,
                    upstream_action_type=dep_cfg['upstream_action_type'],
                    downstream_action_type=dep_cfg['downstream_action_type'],
                )
                dep.fields_passed = dep_cfg.get('fields_passed', [])
                db.session.add(dep)

        db.session.commit()
        logger.info('Seeded artifact dependencies for simulation %s', simulation_id)
    except Exception as e:
        logger.error('seed_artifact_dependencies failed for %s: %s', simulation_id, e)


def _notify_orchestrator(simulation_id: str, stale_count: int):
    try:
        import redis, os
        r = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
        r.publish(f'layer6:{simulation_id}',
                  json.dumps({'event': 'artifacts_stale', 'count': stale_count}))
    except Exception:
        pass
