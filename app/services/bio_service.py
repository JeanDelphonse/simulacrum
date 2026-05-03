import json
import logging
from flask import current_app
from utils.model_router import get_model

logger = logging.getLogger(__name__)

BIO_SYSTEM_PROMPT = """You are a professional biographer writing encyclopedia-style biographical \
articles for accomplished professionals. Your writing must adhere strictly to the conventions \
of Wikipedia biographical articles.

MANDATORY STYLE RULES:
1. Write entirely in the third person. Never use 'I', 'my', 'we', 'you', or any first- or \
second-person pronoun.
2. Open with a lede: a single dense sentence identifying the person by full name, their primary \
professional domain, and the organizations or sectors most associated with their career.
3. Every factual claim must be traceable to the provided source data. Do not invent roles, \
employers, dates, or outcomes.
4. Use specific named deliverables and measurable outcomes where the source data provides them. \
Prefer precision over generality.
5. Structure the bio with these plain-text section markers: \
**Career** — chronological narrative from earliest to most recent role \
**Notable Work** — 2-4 specific projects or deliverables with impact \
**Ventures** — entrepreneurial or independent work (if applicable) \
**Current Work** — present roles and active projects
6. Length: 250-500 words. Each section is a prose paragraph, not a list.
7. Tense: past tense for completed roles and work, present tense for current roles and active ventures.
8. PROHIBITED LANGUAGE — never use these words or their synonyms: passionate, visionary, \
results-driven, dynamic, innovative, thought leader, guru, ninja, rockstar, game-changer, \
disruptive, synergy, leverage (as a verb), cutting-edge, best-in-class.
9. Do not begin any sentence with 'He is known for' or 'She is known for'. State facts directly.
10. Do not speculate about the person's motivations, feelings, or future intentions."""


def generate_wikipedia_bio(profile, resume_text: str, expertise_zones: list) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
    model = get_model('bio_generation')

    zone_summary = json.dumps([
        {
            'zone': z.get('zone_name', str(z)) if isinstance(z, dict) else str(z),
            'evidence': z.get('evidence', '') if isinstance(z, dict) else '',
        }
        for z in (expertise_zones or [])
    ], indent=2)

    user_prompt = (
        f'Write a Wikipedia-style professional biography for the following individual.\n\n'
        f'FULL NAME: {profile.display_name or "Not provided"}\n'
        f'LOCATION: {profile.location or "Not specified"}\n\n'
        f'RESUME / LINKEDIN PROFILE:\n{resume_text[:8000]}\n\n'
        f'EXPERTISE ZONES (extracted from career analysis):\n{zone_summary}\n\n'
        f'Write the bio now. Follow all style rules. Output plain text only. '
        f'Use **Section Name** for section markers. No markdown beyond that.'
    )

    response = client.messages.create(
        model=model,
        max_tokens=1200,
        system=BIO_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': user_prompt}],
    )
    return response.content[0].text


def suggest_zone_tagline(expertise_zone: str, deliverables: list) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
    model = get_model('bio_generation')

    deliv_text = '\n'.join(f'- {d}' for d in (deliverables or [])[:10])
    response = client.messages.create(
        model=model,
        max_tokens=150,
        messages=[{
            'role': 'user',
            'content': (
                f'Write a 1-2 sentence public-facing tagline for a professional offering services in '
                f'"{expertise_zone}". The tagline describes who they help and how.\n\n'
                f'Evidence from their background:\n{deliv_text}\n\n'
                f'Write only the tagline text. No quotes, no labels. Max 200 characters.'
            ),
        }],
    )
    return response.content[0].text.strip()[:200]


def suggest_service_bullets(expertise_zone: str, l1_streams: list, l2_streams: list) -> list:
    import anthropic
    client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
    model = get_model('bio_generation')

    streams = (
        [s.get('name', '') for s in (l1_streams or [])[:3]] +
        [s.get('name', '') for s in (l2_streams or [])[:3]]
    )
    streams_text = '\n'.join(f'- {s}' for s in streams if s)

    response = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[{
            'role': 'user',
            'content': (
                f'Based on these income streams from "{expertise_zone}":\n{streams_text}\n\n'
                f'Write 4-6 short service bullet phrases a consultant might list on their profile. '
                f'Each should be 2-6 words, specific, and actionable. '
                f'Return one per line. No numbers, no dashes, no punctuation at the end.'
            ),
        }],
    )
    lines = [ln.strip().lstrip('-•* ') for ln in response.content[0].text.strip().split('\n') if ln.strip()]
    return [ln[:60] for ln in lines if ln][:6]
