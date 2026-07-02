"""One-time script: add expertise_relevance + description_template to agents.json."""
import json, os, sys

AGENTS_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'agents.json')

# Keywords: consulting training coaching engineering operations marketing
#           finance design sales leadership technology healthcare education legal real_estate
EXTENSIONS = {
    # ── L1 Active Income ──────────────────────────────────────────────────────
    "rate_card": {
        "expertise_relevance": {
            "consulting": 0.90, "training": 0.80, "coaching": 0.80,
            "engineering": 0.75, "operations": 0.85, "marketing": 0.75,
            "finance": 0.75, "design": 0.70, "sales": 0.70,
            "leadership": 0.85, "technology": 0.75, "healthcare": 0.75,
            "education": 0.75, "legal": 0.70, "real_estate": 0.70,
        },
        "description_template": "Rate card and pricing tiers for {primary_expertise} professionals",
    },
    "linkedin_optimization": {
        "expertise_relevance": {
            "consulting": 0.80, "training": 0.75, "coaching": 0.80,
            "engineering": 0.70, "operations": 0.65, "marketing": 0.90,
            "finance": 0.65, "design": 0.75, "sales": 0.85,
            "leadership": 0.85, "technology": 0.75, "healthcare": 0.65,
            "education": 0.70, "legal": 0.65, "real_estate": 0.70,
        },
        "description_template": "LinkedIn headline and profile for {primary_expertise} experts",
    },
    "booking_page": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.90, "coaching": 0.95,
            "engineering": 0.45, "operations": 0.60, "marketing": 0.65,
            "finance": 0.60, "design": 0.80, "sales": 0.65,
            "leadership": 0.60, "technology": 0.50, "healthcare": 0.90,
            "education": 0.90, "legal": 0.75, "real_estate": 0.65,
        },
        "description_template": "Booking page configuration for {primary_expertise} engagements",
    },
    "cold_email_campaign": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.60, "coaching": 0.55,
            "engineering": 0.55, "operations": 0.65, "marketing": 0.85,
            "finance": 0.60, "design": 0.60, "sales": 0.90,
            "leadership": 0.60, "technology": 0.60, "healthcare": 0.50,
            "education": 0.50, "legal": 0.60, "real_estate": 0.70,
        },
        "description_template": "Cold outreach campaign targeting {primary_expertise} buyers",
    },
    "consulting_outreach": {
        "expertise_relevance": {
            "consulting": 0.95, "training": 0.70, "coaching": 0.65,
            "engineering": 0.65, "operations": 0.80, "marketing": 0.80,
            "finance": 0.75, "design": 0.60, "sales": 0.85,
            "leadership": 0.75, "technology": 0.70, "healthcare": 0.65,
            "education": 0.60, "legal": 0.70, "real_estate": 0.70,
        },
        "description_template": "Consulting outreach to {primary_expertise} decision-makers",
    },
    "role_search": {
        "expertise_relevance": {
            "consulting": 0.75, "training": 0.40, "coaching": 0.35,
            "engineering": 0.90, "operations": 0.85, "marketing": 0.70,
            "finance": 0.85, "design": 0.70, "sales": 0.65,
            "leadership": 0.85, "technology": 0.90, "healthcare": 0.65,
            "education": 0.40, "legal": 0.75, "real_estate": 0.50,
        },
        "description_template": "Fractional and contract {primary_expertise} role opportunities",
    },
    "consulting_proposal": {
        "expertise_relevance": {
            "consulting": 0.90, "training": 0.65, "coaching": 0.60,
            "engineering": 0.65, "operations": 0.75, "marketing": 0.70,
            "finance": 0.70, "design": 0.60, "sales": 0.80,
            "leadership": 0.70, "technology": 0.65, "healthcare": 0.60,
            "education": 0.55, "legal": 0.75, "real_estate": 0.60,
        },
        "description_template": "Consulting proposal template for {primary_expertise} projects",
    },
    "sow_template": {
        "expertise_relevance": {
            "consulting": 0.90, "training": 0.60, "coaching": 0.55,
            "engineering": 0.70, "operations": 0.75, "marketing": 0.65,
            "finance": 0.65, "design": 0.70, "sales": 0.65,
            "leadership": 0.65, "technology": 0.70, "healthcare": 0.55,
            "education": 0.50, "legal": 0.85, "real_estate": 0.55,
        },
        "description_template": "Statement of Work template for {primary_expertise} engagements",
    },
    "agreement_template": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.65, "coaching": 0.65,
            "engineering": 0.65, "operations": 0.70, "marketing": 0.65,
            "finance": 0.70, "design": 0.65, "sales": 0.70,
            "leadership": 0.65, "technology": 0.65, "healthcare": 0.65,
            "education": 0.60, "legal": 0.95, "real_estate": 0.70,
        },
        "description_template": "Consulting agreement template for {primary_expertise} clients",
    },
    "referral_network": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.75, "coaching": 0.80,
            "engineering": 0.50, "operations": 0.65, "marketing": 0.75,
            "finance": 0.65, "design": 0.70, "sales": 0.85,
            "leadership": 0.85, "technology": 0.55, "healthcare": 0.80,
            "education": 0.75, "legal": 0.80, "real_estate": 0.85,
        },
        "description_template": "Referral network activation for {primary_expertise} professionals",
    },
    "negotiation_script": {
        "expertise_relevance": {
            "consulting": 0.80, "training": 0.65, "coaching": 0.70,
            "engineering": 0.60, "operations": 0.65, "marketing": 0.65,
            "finance": 0.70, "design": 0.60, "sales": 0.90,
            "leadership": 0.80, "technology": 0.60, "healthcare": 0.60,
            "education": 0.60, "legal": 0.80, "real_estate": 0.75,
        },
        "description_template": "Rate negotiation coaching for {primary_expertise} contracts",
    },
    # ── L2 Leveraged Income ───────────────────────────────────────────────────
    "speaking_proposals": {
        "expertise_relevance": {
            "consulting": 0.80, "training": 0.85, "coaching": 0.75,
            "engineering": 0.65, "operations": 0.65, "marketing": 0.80,
            "finance": 0.65, "design": 0.65, "sales": 0.70,
            "leadership": 0.90, "technology": 0.75, "healthcare": 0.70,
            "education": 0.90, "legal": 0.65, "real_estate": 0.60,
        },
        "description_template": "Speaking proposals for {primary_expertise} conferences and events",
    },
    "speaker_fee_rider": {
        "expertise_relevance": {
            "consulting": 0.75, "training": 0.85, "coaching": 0.70,
            "engineering": 0.55, "operations": 0.60, "marketing": 0.75,
            "finance": 0.60, "design": 0.60, "sales": 0.65,
            "leadership": 0.85, "technology": 0.65, "healthcare": 0.65,
            "education": 0.85, "legal": 0.65, "real_estate": 0.55,
        },
        "description_template": "Speaker fee structure for {primary_expertise} keynotes",
    },
    "group_coaching": {
        "expertise_relevance": {
            "consulting": 0.65, "training": 0.85, "coaching": 0.95,
            "engineering": 0.40, "operations": 0.55, "marketing": 0.65,
            "finance": 0.60, "design": 0.55, "sales": 0.65,
            "leadership": 0.80, "technology": 0.50, "healthcare": 0.75,
            "education": 0.85, "legal": 0.50, "real_estate": 0.50,
        },
        "description_template": "Group coaching program for {primary_expertise} professionals",
    },
    "workshop_curriculum": {
        "expertise_relevance": {
            "consulting": 0.75, "training": 0.95, "coaching": 0.85,
            "engineering": 0.55, "operations": 0.70, "marketing": 0.65,
            "finance": 0.55, "design": 0.65, "sales": 0.65,
            "leadership": 0.80, "technology": 0.60, "healthcare": 0.75,
            "education": 0.95, "legal": 0.55, "real_estate": 0.50,
        },
        "description_template": "Workshop curriculum for {primary_expertise} training programs",
    },
    "corporate_training": {
        "expertise_relevance": {
            "consulting": 0.80, "training": 0.95, "coaching": 0.75,
            "engineering": 0.60, "operations": 0.75, "marketing": 0.70,
            "finance": 0.65, "design": 0.60, "sales": 0.75,
            "leadership": 0.90, "technology": 0.65, "healthcare": 0.75,
            "education": 0.90, "legal": 0.60, "real_estate": 0.50,
        },
        "description_template": "Corporate training program for {primary_expertise} teams",
    },
    "waitlist_landing_page": {
        "expertise_relevance": {
            "consulting": 0.55, "training": 0.75, "coaching": 0.80,
            "engineering": 0.55, "operations": 0.55, "marketing": 0.85,
            "finance": 0.50, "design": 0.80, "sales": 0.75,
            "leadership": 0.55, "technology": 0.65, "healthcare": 0.65,
            "education": 0.75, "legal": 0.45, "real_estate": 0.50,
        },
        "description_template": "Waitlist landing page for your {primary_expertise} program launch",
    },
    "alumni_reactivation": {
        "expertise_relevance": {
            "consulting": 0.60, "training": 0.85, "coaching": 0.80,
            "engineering": 0.40, "operations": 0.50, "marketing": 0.70,
            "finance": 0.50, "design": 0.55, "sales": 0.60,
            "leadership": 0.60, "technology": 0.45, "healthcare": 0.65,
            "education": 0.85, "legal": 0.45, "real_estate": 0.45,
        },
        "description_template": "Alumni reactivation for past {primary_expertise} students and clients",
    },
    "roi_calculator": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.90, "coaching": 0.70,
            "engineering": 0.60, "operations": 0.80, "marketing": 0.65,
            "finance": 0.85, "design": 0.55, "sales": 0.85,
            "leadership": 0.75, "technology": 0.60, "healthcare": 0.65,
            "education": 0.80, "legal": 0.60, "real_estate": 0.60,
        },
        "description_template": "ROI calculator for {primary_expertise} programs and services",
    },
    # ── L3 Digital Products ───────────────────────────────────────────────────
    "course_curriculum": {
        "expertise_relevance": {
            "consulting": 0.75, "training": 0.90, "coaching": 0.85,
            "engineering": 0.65, "operations": 0.70, "marketing": 0.70,
            "finance": 0.65, "design": 0.70, "sales": 0.65,
            "leadership": 0.75, "technology": 0.70, "healthcare": 0.75,
            "education": 0.95, "legal": 0.65, "real_estate": 0.60,
        },
        "description_template": "Online course curriculum for {primary_expertise} learners",
    },
    "pricing_research": {
        "expertise_relevance": {
            "consulting": 0.70, "training": 0.75, "coaching": 0.70,
            "engineering": 0.60, "operations": 0.65, "marketing": 0.80,
            "finance": 0.80, "design": 0.65, "sales": 0.80,
            "leadership": 0.65, "technology": 0.65, "healthcare": 0.60,
            "education": 0.70, "legal": 0.55, "real_estate": 0.60,
        },
        "description_template": "Pricing research for {primary_expertise} digital products",
    },
    "sales_page": {
        "expertise_relevance": {
            "consulting": 0.65, "training": 0.80, "coaching": 0.80,
            "engineering": 0.50, "operations": 0.55, "marketing": 0.90,
            "finance": 0.55, "design": 0.80, "sales": 0.90,
            "leadership": 0.60, "technology": 0.55, "healthcare": 0.65,
            "education": 0.80, "legal": 0.50, "real_estate": 0.55,
        },
        "description_template": "Sales page for {primary_expertise} course or program",
    },
    "ebook_outline": {
        "expertise_relevance": {
            "consulting": 0.75, "training": 0.85, "coaching": 0.80,
            "engineering": 0.65, "operations": 0.70, "marketing": 0.75,
            "finance": 0.65, "design": 0.65, "sales": 0.60,
            "leadership": 0.75, "technology": 0.70, "healthcare": 0.70,
            "education": 0.85, "legal": 0.65, "real_estate": 0.60,
        },
        "description_template": "E-book outline on {primary_expertise} for your target audience",
    },
    "ab_test_plan": {
        "expertise_relevance": {
            "consulting": 0.50, "training": 0.50, "coaching": 0.45,
            "engineering": 0.75, "operations": 0.65, "marketing": 0.90,
            "finance": 0.65, "design": 0.75, "sales": 0.75,
            "leadership": 0.45, "technology": 0.80, "healthcare": 0.45,
            "education": 0.50, "legal": 0.40, "real_estate": 0.45,
        },
        "description_template": "A/B pricing experiment for {primary_expertise} products",
    },
    "membership": {
        "expertise_relevance": {
            "consulting": 0.60, "training": 0.75, "coaching": 0.85,
            "engineering": 0.50, "operations": 0.55, "marketing": 0.75,
            "finance": 0.55, "design": 0.60, "sales": 0.65,
            "leadership": 0.75, "technology": 0.60, "healthcare": 0.65,
            "education": 0.85, "legal": 0.50, "real_estate": 0.55,
        },
        "description_template": "Membership community structure for {primary_expertise} practitioners",
    },
    "launch_sequence": {
        "expertise_relevance": {
            "consulting": 0.55, "training": 0.75, "coaching": 0.75,
            "engineering": 0.45, "operations": 0.55, "marketing": 0.90,
            "finance": 0.50, "design": 0.65, "sales": 0.80,
            "leadership": 0.55, "technology": 0.55, "healthcare": 0.55,
            "education": 0.75, "legal": 0.45, "real_estate": 0.50,
        },
        "description_template": "7-email launch sequence for your {primary_expertise} product",
    },
    "affiliate_program": {
        "expertise_relevance": {
            "consulting": 0.50, "training": 0.65, "coaching": 0.65,
            "engineering": 0.45, "operations": 0.50, "marketing": 0.85,
            "finance": 0.50, "design": 0.60, "sales": 0.80,
            "leadership": 0.50, "technology": 0.55, "healthcare": 0.50,
            "education": 0.65, "legal": 0.45, "real_estate": 0.50,
        },
        "description_template": "Affiliate program for {primary_expertise} course or product",
    },
    "testimonials": {
        "expertise_relevance": {
            "consulting": 0.80, "training": 0.80, "coaching": 0.85,
            "engineering": 0.55, "operations": 0.65, "marketing": 0.80,
            "finance": 0.60, "design": 0.75, "sales": 0.75,
            "leadership": 0.70, "technology": 0.60, "healthcare": 0.80,
            "education": 0.80, "legal": 0.65, "real_estate": 0.70,
        },
        "description_template": "Testimonial and case study system for {primary_expertise} clients",
    },
    "lapsed_buyer": {
        "expertise_relevance": {
            "consulting": 0.55, "training": 0.70, "coaching": 0.70,
            "engineering": 0.40, "operations": 0.50, "marketing": 0.80,
            "finance": 0.50, "design": 0.60, "sales": 0.75,
            "leadership": 0.50, "technology": 0.50, "healthcare": 0.55,
            "education": 0.70, "legal": 0.45, "real_estate": 0.50,
        },
        "description_template": "Reactivation campaign for lapsed {primary_expertise} buyers",
    },
    # ── L4 Automation & IP ────────────────────────────────────────────────────
    "seo_content_calendar": {
        "expertise_relevance": {
            "consulting": 0.65, "training": 0.70, "coaching": 0.70,
            "engineering": 0.55, "operations": 0.65, "marketing": 0.90,
            "finance": 0.55, "design": 0.65, "sales": 0.70,
            "leadership": 0.60, "technology": 0.65, "healthcare": 0.60,
            "education": 0.70, "legal": 0.55, "real_estate": 0.65,
        },
        "description_template": "90-day SEO content calendar for {primary_expertise} authority",
    },
    "lead_magnet_funnel": {
        "expertise_relevance": {
            "consulting": 0.70, "training": 0.75, "coaching": 0.80,
            "engineering": 0.50, "operations": 0.60, "marketing": 0.90,
            "finance": 0.55, "design": 0.65, "sales": 0.80,
            "leadership": 0.60, "technology": 0.60, "healthcare": 0.65,
            "education": 0.75, "legal": 0.50, "real_estate": 0.60,
        },
        "description_template": "Lead magnet funnel for {primary_expertise} audience",
    },
    "newsletter": {
        "expertise_relevance": {
            "consulting": 0.70, "training": 0.75, "coaching": 0.70,
            "engineering": 0.65, "operations": 0.65, "marketing": 0.80,
            "finance": 0.70, "design": 0.65, "sales": 0.65,
            "leadership": 0.80, "technology": 0.75, "healthcare": 0.65,
            "education": 0.80, "legal": 0.65, "real_estate": 0.65,
        },
        "description_template": "Newsletter monetization strategy for {primary_expertise} readers",
    },
    "saas_product_spec": {
        "expertise_relevance": {
            "consulting": 0.30, "training": 0.20, "coaching": 0.20,
            "engineering": 0.95, "operations": 0.45, "marketing": 0.40,
            "finance": 0.35, "design": 0.50, "sales": 0.35,
            "leadership": 0.40, "technology": 0.90, "healthcare": 0.30,
            "education": 0.30, "legal": 0.25, "real_estate": 0.20,
        },
        "description_template": "SaaS product spec for {primary_expertise} workflow automation",
    },
    "ip_licensing": {
        "expertise_relevance": {
            "consulting": 0.55, "training": 0.60, "coaching": 0.55,
            "engineering": 0.60, "operations": 0.55, "marketing": 0.50,
            "finance": 0.55, "design": 0.60, "sales": 0.50,
            "leadership": 0.55, "technology": 0.65, "healthcare": 0.50,
            "education": 0.60, "legal": 0.85, "real_estate": 0.50,
        },
        "description_template": "IP licensing strategy for {primary_expertise} frameworks and systems",
    },
    "affiliate_partnerships": {
        "expertise_relevance": {
            "consulting": 0.60, "training": 0.65, "coaching": 0.65,
            "engineering": 0.45, "operations": 0.55, "marketing": 0.85,
            "finance": 0.50, "design": 0.55, "sales": 0.80,
            "leadership": 0.55, "technology": 0.55, "healthcare": 0.50,
            "education": 0.65, "legal": 0.45, "real_estate": 0.55,
        },
        "description_template": "Affiliate partnership opportunities in the {primary_expertise} ecosystem",
    },
    "video_podcast": {
        "expertise_relevance": {
            "consulting": 0.65, "training": 0.80, "coaching": 0.75,
            "engineering": 0.60, "operations": 0.55, "marketing": 0.80,
            "finance": 0.60, "design": 0.70, "sales": 0.65,
            "leadership": 0.80, "technology": 0.70, "healthcare": 0.70,
            "education": 0.80, "legal": 0.60, "real_estate": 0.60,
        },
        "description_template": "YouTube and podcast strategy for {primary_expertise} thought leadership",
    },
    "community": {
        "expertise_relevance": {
            "consulting": 0.65, "training": 0.80, "coaching": 0.85,
            "engineering": 0.55, "operations": 0.60, "marketing": 0.75,
            "finance": 0.55, "design": 0.65, "sales": 0.65,
            "leadership": 0.80, "technology": 0.65, "healthcare": 0.70,
            "education": 0.85, "legal": 0.55, "real_estate": 0.55,
        },
        "description_template": "Community flywheel for {primary_expertise} practitioners",
    },
    "programmatic_ads": {
        "expertise_relevance": {
            "consulting": 0.40, "training": 0.40, "coaching": 0.40,
            "engineering": 0.65, "operations": 0.45, "marketing": 0.90,
            "finance": 0.50, "design": 0.60, "sales": 0.70,
            "leadership": 0.40, "technology": 0.70, "healthcare": 0.50,
            "education": 0.50, "legal": 0.40, "real_estate": 0.60,
        },
        "description_template": "Programmatic ad strategy targeting {primary_expertise} buyers",
    },
    "client_winback": {
        "expertise_relevance": {
            "consulting": 0.80, "training": 0.75, "coaching": 0.75,
            "engineering": 0.50, "operations": 0.65, "marketing": 0.75,
            "finance": 0.60, "design": 0.65, "sales": 0.80,
            "leadership": 0.65, "technology": 0.55, "healthcare": 0.70,
            "education": 0.70, "legal": 0.65, "real_estate": 0.70,
        },
        "description_template": "Win-back campaign for lapsed {primary_expertise} clients",
    },
    # ── L5 Wealth Deployment (universal — all scores high) ───────────────────
    "income_allocation": {
        "expertise_relevance": {
            "consulting": 0.90, "training": 0.85, "coaching": 0.85,
            "engineering": 0.85, "operations": 0.85, "marketing": 0.85,
            "finance": 0.95, "design": 0.80, "sales": 0.85,
            "leadership": 0.90, "technology": 0.85, "healthcare": 0.85,
            "education": 0.85, "legal": 0.85, "real_estate": 0.90,
        },
        "description_template": "Income allocation strategy for {primary_expertise} professionals",
    },
    "projections": {
        "expertise_relevance": {
            "consulting": 0.88, "training": 0.85, "coaching": 0.85,
            "engineering": 0.85, "operations": 0.85, "marketing": 0.83,
            "finance": 0.95, "design": 0.80, "sales": 0.85,
            "leadership": 0.88, "technology": 0.85, "healthcare": 0.85,
            "education": 0.85, "legal": 0.85, "real_estate": 0.90,
        },
        "description_template": "Compound growth projections for {primary_expertise} income",
    },
    "fund_recommendations": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.83, "coaching": 0.83,
            "engineering": 0.85, "operations": 0.83, "marketing": 0.80,
            "finance": 0.95, "design": 0.80, "sales": 0.83,
            "leadership": 0.85, "technology": 0.85, "healthcare": 0.83,
            "education": 0.83, "legal": 0.83, "real_estate": 0.85,
        },
        "description_template": "Index fund and ETF portfolio for {primary_expertise} professionals",
    },
    "investment_policy": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.83, "coaching": 0.83,
            "engineering": 0.83, "operations": 0.83, "marketing": 0.80,
            "finance": 0.95, "design": 0.80, "sales": 0.83,
            "leadership": 0.85, "technology": 0.83, "healthcare": 0.83,
            "education": 0.83, "legal": 0.85, "real_estate": 0.88,
        },
        "description_template": "Investment policy statement for {primary_expertise} professionals",
    },
    "real_estate_strategy": {
        "expertise_relevance": {
            "consulting": 0.75, "training": 0.73, "coaching": 0.73,
            "engineering": 0.75, "operations": 0.75, "marketing": 0.73,
            "finance": 0.90, "design": 0.75, "sales": 0.78,
            "leadership": 0.78, "technology": 0.75, "healthcare": 0.75,
            "education": 0.73, "legal": 0.80, "real_estate": 0.98,
        },
        "description_template": "Real estate entry strategy for {primary_expertise} professionals",
    },
    "entity_structure": {
        "expertise_relevance": {
            "consulting": 0.90, "training": 0.83, "coaching": 0.83,
            "engineering": 0.83, "operations": 0.85, "marketing": 0.80,
            "finance": 0.90, "design": 0.80, "sales": 0.83,
            "leadership": 0.88, "technology": 0.83, "healthcare": 0.85,
            "education": 0.83, "legal": 0.95, "real_estate": 0.88,
        },
        "description_template": "Business entity structure for {primary_expertise} practice",
    },
    "tax_optimization": {
        "expertise_relevance": {
            "consulting": 0.90, "training": 0.85, "coaching": 0.85,
            "engineering": 0.85, "operations": 0.85, "marketing": 0.83,
            "finance": 0.98, "design": 0.82, "sales": 0.85,
            "leadership": 0.88, "technology": 0.85, "healthcare": 0.85,
            "education": 0.85, "legal": 0.90, "real_estate": 0.90,
        },
        "description_template": "Tax optimization strategy for {primary_expertise} income",
    },
    "dca_schedule": {
        "expertise_relevance": {
            "consulting": 0.85, "training": 0.83, "coaching": 0.83,
            "engineering": 0.85, "operations": 0.83, "marketing": 0.80,
            "finance": 0.95, "design": 0.80, "sales": 0.83,
            "leadership": 0.85, "technology": 0.85, "healthcare": 0.83,
            "education": 0.83, "legal": 0.83, "real_estate": 0.88,
        },
        "description_template": "Dollar-cost averaging schedule for {primary_expertise} professionals",
    },
    "insurance_review": {
        "expertise_relevance": {
            "consulting": 0.83, "training": 0.80, "coaching": 0.80,
            "engineering": 0.80, "operations": 0.80, "marketing": 0.78,
            "finance": 0.90, "design": 0.78, "sales": 0.80,
            "leadership": 0.83, "technology": 0.80, "healthcare": 0.95,
            "education": 0.80, "legal": 0.83, "real_estate": 0.83,
        },
        "description_template": "Insurance gap analysis for {primary_expertise} professionals",
    },
    "estate_planning": {
        "expertise_relevance": {
            "consulting": 0.83, "training": 0.80, "coaching": 0.80,
            "engineering": 0.80, "operations": 0.80, "marketing": 0.78,
            "finance": 0.93, "design": 0.78, "sales": 0.80,
            "leadership": 0.85, "technology": 0.80, "healthcare": 0.83,
            "education": 0.80, "legal": 0.95, "real_estate": 0.90,
        },
        "description_template": "Estate planning checklist for {primary_expertise} professionals",
    },
}


def main():
    with open(AGENTS_JSON, 'r', encoding='utf-8') as fh:
        data = json.load(fh)

    updated = 0
    missing = []
    for agent in data['agents']:
        at = agent['action_type']
        if at in EXTENSIONS:
            agent['expertise_relevance'] = EXTENSIONS[at]['expertise_relevance']
            agent['description_template'] = EXTENSIONS[at]['description_template']
            updated += 1
        else:
            missing.append(at)

    if missing:
        print(f'WARNING — no extension defined for: {missing}', file=sys.stderr)

    with open(AGENTS_JSON, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    print(f'Updated {updated}/{len(data["agents"])} agents in agents.json')


if __name__ == '__main__':
    main()
