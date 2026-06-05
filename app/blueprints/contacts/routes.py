import csv
import io
import logging
from datetime import datetime

from flask import jsonify, request, current_app
from flask_login import login_required, current_user

from app.blueprints.contacts import contacts_bp
from app.extensions import db
from app.models.contact import Contact, ContactActivity, STAGE_ORDER
from app.services.contact_scoring import AGENT_MIN_SCORES
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


def _queue_score(contact_id: str):
    try:
        from app.tasks.contacts import score_contact
        score_contact.delay(contact_id)
    except Exception as e:
        logger.debug('Could not queue scoring task: %s', e)


# ── List & Create ─────────────────────────────────────────────────────────────

@contacts_bp.route('', methods=['GET'])
@login_required
def list_contacts():
    q = Contact.query.filter_by(user_id=current_user.id)

    stage = request.args.get('stage')
    if stage:
        q = q.filter(Contact.pipeline_stage == stage)

    archived = request.args.get('archived', '0')
    if archived != '1':
        q = q.filter(Contact.is_archived == False)

    min_score = request.args.get('min_score', type=float)
    if min_score is not None:
        q = q.filter(Contact.qualifying_score >= min_score)

    search = request.args.get('q', '').strip()
    if search:
        like = f'%{search}%'
        q = q.filter(
            db.or_(
                Contact.first_name.ilike(like),
                Contact.last_name.ilike(like),
                Contact.email.ilike(like),
                Contact.company_name.ilike(like),
            )
        )

    source = request.args.get('source')
    if source:
        q = q.filter(Contact.source == source)

    industry = request.args.get('industry')
    if industry:
        q = q.filter(Contact.industry.ilike(f'%{industry}%'))

    sort = request.args.get('sort', 'score')
    if sort == 'score':
        # MySQL <8.0.30 has no NULLS LAST — coalesce pushes NULLs to the bottom
        q = q.order_by(db.func.coalesce(Contact.qualifying_score, -1).desc())
    elif sort == 'name':
        q = q.order_by(Contact.last_name, Contact.first_name)
    elif sort == 'activity':
        q = q.order_by(
            db.case((Contact.last_contacted_at.is_(None), 1), else_=0),
            Contact.last_contacted_at.desc(),
        )
    elif sort == 'stage':
        q = q.order_by(Contact.pipeline_stage)
    else:
        q = q.order_by(Contact.created_at.desc())

    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    paginated = q.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'contacts': [c.to_dict() for c in paginated.items],
        'total': paginated.total,
        'page': page,
        'pages': paginated.pages,
    })


@contacts_bp.route('', methods=['POST'])
@login_required
def create_contact():
    data = request.get_json(force=True) or {}

    email = (data.get('email') or '').strip().lower()
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()

    if not email or not first_name or not last_name:
        return jsonify({'error': 'first_name, last_name, and email are required'}), 400

    existing = Contact.query.filter_by(user_id=current_user.id, email=email).first()
    if existing:
        return jsonify({'error': 'A contact with that email already exists', 'contact_id': existing.id}), 409

    contact = Contact(
        id=generate_id(),
        user_id=current_user.id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=data.get('phone'),
        job_title=data.get('job_title'),
        company_name=data.get('company_name'),
        company_size=data.get('company_size'),
        industry=data.get('industry'),
        department=data.get('department'),
        seniority=data.get('seniority'),
        linkedin_url=data.get('linkedin_url'),
        linkedin_headline=data.get('linkedin_headline'),
        website_url=data.get('website_url'),
        company_website=data.get('company_website'),
        twitter_url=data.get('twitter_url'),
        other_url=data.get('other_url'),
        city=data.get('city'),
        state_region=data.get('state_region'),
        country=data.get('country', 'United States'),
        timezone=data.get('timezone'),
        source=data.get('source', 'manual_entry'),
        source_notes=data.get('source_notes'),
        notes=data.get('notes'),
        pipeline_stage='prospect',
    )
    db.session.add(contact)
    db.session.commit()
    _queue_score(contact.id)
    return jsonify(contact.to_dict()), 201


# ── Single Contact ────────────────────────────────────────────────────────────

@contacts_bp.route('/<contact_id>', methods=['GET'])
@login_required
def get_contact(contact_id):
    contact = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    data = contact.to_dict()
    data['activities'] = [a.to_dict() for a in contact.get_activities(50)]
    return jsonify(data)


@contacts_bp.route('/<contact_id>', methods=['PUT', 'PATCH'])
@login_required
def update_contact(contact_id):
    contact = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    data = request.get_json(force=True) or {}

    updatable = [
        'first_name', 'last_name', 'phone', 'job_title', 'company_name', 'company_size',
        'industry', 'department', 'seniority', 'linkedin_url', 'linkedin_headline',
        'website_url', 'company_website', 'twitter_url', 'other_url',
        'city', 'state_region', 'country', 'timezone', 'source_notes', 'notes',
    ]
    changed = False
    for field in updatable:
        if field in data:
            setattr(contact, field, data[field])
            changed = True

    if 'email' in data:
        new_email = data['email'].strip().lower()
        if new_email != contact.email:
            dup = Contact.query.filter_by(user_id=current_user.id, email=new_email).first()
            if dup:
                return jsonify({'error': 'Email already used by another contact'}), 409
            contact.email = new_email
            changed = True

    if 'pipeline_stage' in data:
        new_stage = data['pipeline_stage']
        contact.advance_stage(new_stage, created_by='user')
        changed = True

    if 'do_not_contact' in data:
        contact.do_not_contact = bool(data['do_not_contact'])
        changed = True

    if 'is_archived' in data:
        contact.is_archived = bool(data['is_archived'])
        changed = True

    if changed:
        db.session.commit()
        if any(f in data for f in ['seniority', 'company_size', 'industry', 'job_title']):
            _queue_score(contact.id)

    return jsonify(contact.to_dict())


@contacts_bp.route('/<contact_id>', methods=['DELETE'])
@login_required
def delete_contact(contact_id):
    contact = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    contact.is_archived = True
    db.session.commit()
    return jsonify({'ok': True})


# ── Stage Advance ─────────────────────────────────────────────────────────────

@contacts_bp.route('/<contact_id>/stage', methods=['POST'])
@login_required
def advance_stage(contact_id):
    contact = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    data = request.get_json(force=True) or {}
    new_stage = data.get('stage')
    if not new_stage or new_stage not in ['prospect', 'active', 'client', 'closed_lost']:
        return jsonify({'error': 'Invalid stage'}), 400

    contact.advance_stage(new_stage, created_by='user', notes=data.get('notes'))
    db.session.commit()
    return jsonify(contact.to_dict())


# ── Activities ────────────────────────────────────────────────────────────────

@contacts_bp.route('/<contact_id>/activities', methods=['GET'])
@login_required
def list_activities(contact_id):
    contact = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    activities = contact.get_activities(limit=200)
    return jsonify([a.to_dict() for a in activities])


@contacts_bp.route('/<contact_id>/activities', methods=['POST'])
@login_required
def add_activity(contact_id):
    contact = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    data = request.get_json(force=True) or {}
    activity_type = data.get('activity_type', 'note_added')

    activity = ContactActivity(
        id=generate_id(),
        contact_id=contact.id,
        simulation_id=data.get('simulation_id'),
        activity_type=activity_type,
        notes=data.get('notes'),
        pipeline_stage_from=data.get('pipeline_stage_from'),
        pipeline_stage_to=data.get('pipeline_stage_to'),
        created_by='user',
    )
    db.session.add(activity)

    if activity_type in ('email_sent', 'call_completed', 'meeting_completed', 'manually_contacted'):
        contact.last_contacted_at = datetime.utcnow()

    db.session.commit()
    return jsonify(activity.to_dict()), 201


# ── Bulk Actions ──────────────────────────────────────────────────────────────

@contacts_bp.route('/bulk', methods=['POST'])
@login_required
def bulk_action():
    data = request.get_json(force=True) or {}
    action = data.get('action')
    ids = data.get('ids', [])
    if not ids or not action:
        return jsonify({'error': 'action and ids required'}), 400

    contacts = Contact.query.filter(
        Contact.id.in_(ids),
        Contact.user_id == current_user.id,
    ).all()

    updated = 0
    if action == 'archive':
        for c in contacts:
            c.is_archived = True
            updated += 1
    elif action == 'do_not_contact':
        for c in contacts:
            c.do_not_contact = True
            updated += 1
    elif action == 'advance_stage':
        new_stage = data.get('stage')
        if not new_stage:
            return jsonify({'error': 'stage required for advance_stage'}), 400
        for c in contacts:
            if c.advance_stage(new_stage, created_by='user'):
                updated += 1
    else:
        return jsonify({'error': f'Unknown action: {action}'}), 400

    db.session.commit()
    return jsonify({'updated': updated})


# ── Promote from Agent Artifact ───────────────────────────────────────────────

@contacts_bp.route('/promote-batch', methods=['POST'])
@login_required
def promote_batch():
    """Promote agent-generated contacts to DB. Deduplicates by email."""
    data = request.get_json(force=True) or {}
    contact_list = data.get('contacts', [])
    action_id = data.get('action_id')
    simulation_id = data.get('simulation_id')

    created = updated = skipped = 0
    for item in contact_list:
        email = (item.get('email') or '').strip().lower()
        first_name = (item.get('first_name') or '').strip()
        last_name = (item.get('last_name') or '').strip()
        if not email or not first_name or not last_name:
            skipped += 1
            continue

        existing = Contact.query.filter_by(user_id=current_user.id, email=email).first()
        if existing:
            updated += 1
            continue

        contact = Contact(
            id=generate_id(),
            user_id=current_user.id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            job_title=item.get('job_title'),
            company_name=item.get('company_name'),
            company_size=item.get('company_size'),
            industry=item.get('industry'),
            seniority=item.get('seniority'),
            source='agent_generated',
            source_action_id=action_id,
            pipeline_stage='prospect',
        )
        db.session.add(contact)
        db.session.flush()
        _queue_score(contact.id)
        created += 1

    db.session.commit()
    return jsonify({'created': created, 'updated': updated, 'skipped': skipped})


# ── CSV Import ────────────────────────────────────────────────────────────────

@contacts_bp.route('/import/csv', methods=['POST'])
@login_required
def import_csv():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if not f.filename.endswith(('.csv', '.tsv')):
        return jsonify({'error': 'Only CSV/TSV files accepted'}), 400

    content = f.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)

    if len(rows) > 5000:
        return jsonify({'error': 'Maximum 5,000 rows per import'}), 400

    created = updated = skipped = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        email = (row.get('email') or row.get('Email') or '').strip().lower()
        first_name = (row.get('first_name') or row.get('First Name') or row.get('FirstName') or '').strip()
        last_name = (row.get('last_name') or row.get('Last Name') or row.get('LastName') or '').strip()

        if not email:
            errors.append(f'Row {i}: missing email')
            skipped += 1
            continue
        if not first_name or not last_name:
            errors.append(f'Row {i}: missing first_name or last_name')
            skipped += 1
            continue

        existing = Contact.query.filter_by(user_id=current_user.id, email=email).first()
        if existing:
            for field, csv_key in [
                ('job_title', 'job_title'), ('company_name', 'company_name'),
                ('industry', 'industry'), ('phone', 'phone'),
            ]:
                val = row.get(csv_key, '').strip()
                if val and not getattr(existing, field):
                    setattr(existing, field, val)
            updated += 1
            continue

        contact = Contact(
            id=generate_id(),
            user_id=current_user.id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=(row.get('phone') or row.get('Phone') or '').strip() or None,
            job_title=(row.get('job_title') or row.get('Job Title') or '').strip() or None,
            company_name=(row.get('company_name') or row.get('Company') or '').strip() or None,
            company_size=(row.get('company_size') or '').strip() or None,
            industry=(row.get('industry') or row.get('Industry') or '').strip() or None,
            seniority=(row.get('seniority') or '').strip() or None,
            linkedin_url=(row.get('linkedin_url') or row.get('LinkedIn') or '').strip() or None,
            city=(row.get('city') or row.get('City') or '').strip() or None,
            country=(row.get('country') or 'United States').strip(),
            source='csv_import',
            pipeline_stage='prospect',
        )
        db.session.add(contact)
        db.session.flush()
        _queue_score(contact.id)
        created += 1

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error('CSV import commit failed: %s', e)
        return jsonify({'error': 'Import failed — database error'}), 500

    return jsonify({
        'created': created,
        'updated': updated,
        'skipped': skipped,
        'errors': errors[:20],
    })


# ── Duplicate Check ───────────────────────────────────────────────────────────

@contacts_bp.route('/check-email', methods=['GET'])
@login_required
def check_email():
    email = (request.args.get('email') or '').strip().lower()
    if not email:
        return jsonify({'exists': False})
    existing = Contact.query.filter_by(user_id=current_user.id, email=email).first()
    if existing:
        return jsonify({'exists': True, 'contact_id': existing.id,
                        'display_name': existing.display_name,
                        'pipeline_stage': existing.pipeline_stage})
    return jsonify({'exists': False})
