"""Careers routes.

Public:
  POST /api/careers/apply                              multipart application intake

Admin:
  GET    /api/admin/careers/applications               list (filter by job / status / search)
  GET    /api/admin/careers/applications/<id>          detail, including extracted resume text
  PATCH  /api/admin/careers/applications/<id>          status / note
  GET    /api/admin/careers/applications/<id>/resume   inline preview or download of the file
  DELETE /api/admin/careers/applications/<id>          removes the row and the file on disk
"""
from __future__ import annotations
import logging
import os
import re
import time
from datetime import datetime
from functools import wraps

from flask import request, jsonify, send_file, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.blueprints.careers import careers_bp
from app.extensions import db
from app.models.job_application import JobApplication
from app.models.audit_log import AuditLog
from app.services import careers_service
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
_PHONE_RE = re.compile(r'^[\d\s()+.\-]{7,25}$')

# Soft per-IP throttle. This is an unauthenticated upload endpoint, so cap how
# many attempts one address can make in an hour. The cap is well above what a real
# applicant needs (there are four roles) — it exists to stop a bot writing files.
# In-process only, which is enough for a single Passenger worker and fails open on
# restart.
_RATE_WINDOW_SEC = 3600
_RATE_MAX = 20
_recent_submits: dict = {}


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


def _client_ip() -> str:
    fwd = request.headers.get('X-Forwarded-For', '')
    return (fwd.split(',')[0].strip() or request.remote_addr or '')[:45]


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _recent_submits.get(ip, []) if now - t < _RATE_WINDOW_SEC]
    # Drop stale buckets so the dict cannot grow without bound.
    for key in [k for k, v in _recent_submits.items() if not any(now - t < _RATE_WINDOW_SEC for t in v)]:
        _recent_submits.pop(key, None)
    if len(hits) >= _RATE_MAX:
        _recent_submits[ip] = hits
        return True
    hits.append(now)
    _recent_submits[ip] = hits
    return False


# ── Public: apply ───────────────────────────────────────────────────────────

@careers_bp.route('/api/careers/apply', methods=['POST'])
def apply():
    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({'error': 'Too many attempts from this connection. Please try again later.'}), 429

    job_slug  = (request.form.get('job_slug') or '').strip()
    full_name = (request.form.get('full_name') or '').strip()
    email     = (request.form.get('email') or '').strip().lower()
    phone     = (request.form.get('phone') or '').strip()

    job = careers_service.get_job(job_slug)
    if not job:
        return jsonify({'error': 'Unknown role. Please pick a role from the careers page.'}), 400
    if not full_name or len(full_name) < 2:
        return jsonify({'error': 'Please enter your full name.'}), 400
    if not _EMAIL_RE.match(email):
        return jsonify({'error': 'Please enter a valid email address.'}), 400
    if not _PHONE_RE.match(phone):
        return jsonify({'error': 'Please enter a valid phone number.'}), 400

    file = request.files.get('resume')
    if not file or not file.filename:
        return jsonify({'error': 'Please attach your resume.'}), 400
    ext = careers_service.resume_extension(file.filename)
    if not ext:
        return jsonify({'error': 'Resume must be a PDF or Word document (.pdf, .docx, .doc).'}), 400

    existing = JobApplication.query.filter_by(email=email, job_slug=job_slug).first()
    if existing:
        return jsonify({'error': 'You have already applied for this role. We have your application.'}), 409

    filename = secure_filename(file.filename) or f'resume.{ext}'
    stored_name = f'{generate_id()}_{filename}'
    file_path = os.path.join(careers_service.resume_upload_dir(), stored_name)
    file.save(file_path)

    size = None
    try:
        size = os.path.getsize(file_path)
    except OSError:
        pass

    application = JobApplication(
        id=generate_id(),
        job_slug=job_slug,
        job_title=job['title'],
        full_name=full_name[:160],
        email=email[:160],
        phone=phone[:40],
        resume_filename=filename[:255],
        resume_path=file_path,
        resume_type=ext,
        resume_size=size,
        resume_text=careers_service.extract_resume_text(file_path, ext)[:200000] or None,
        status=JobApplication.STATUS_NEW,
        source_ip=ip,
        user_agent=(request.headers.get('User-Agent') or '')[:500],
    )
    db.session.add(application)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Do not leave an orphan file behind if the row never landed.
        try:
            os.remove(file_path)
        except OSError:
            pass
        raise

    _dispatch_application_emails(application)

    return jsonify({
        'id': application.id,
        'message': 'Application received. We review every application and will be in touch by email.',
    }), 201


def _dispatch_application_emails(application: JobApplication):
    """Confirm to the applicant, notify the admin. Best-effort — email trouble
    must not fail an application that is already stored."""
    try:
        from app.models.platform_settings import PlatformSetting
        from app.services.email_service import (
            send_job_application_received_email,
            send_admin_new_job_application_email,
        )
        send_job_application_received_email(
            application.email, application.full_name, application.job_title,
        )
        admin_email = PlatformSetting.get('admin_email', None)
        if admin_email:
            send_admin_new_job_application_email(
                admin_email, application.full_name, application.email,
                application.phone, application.job_title, application.resume_filename,
            )
    except Exception as exc:
        logger.warning('Careers: application emails failed for %s: %s', application.id, exc)


# ── Admin: review ───────────────────────────────────────────────────────────

@careers_bp.route('/api/admin/careers/applications', methods=['GET'])
@login_required
@admin_required
def list_applications():
    job_slug = (request.args.get('job') or '').strip()
    status   = (request.args.get('status') or '').strip()
    search   = (request.args.get('q') or '').strip()

    query = JobApplication.query
    if job_slug:
        query = query.filter_by(job_slug=job_slug)
    if status:
        query = query.filter_by(status=status)
    if search:
        like = f'%{search}%'
        query = query.filter(db.or_(
            JobApplication.full_name.ilike(like),
            JobApplication.email.ilike(like),
            JobApplication.phone.ilike(like),
        ))

    rows = query.order_by(JobApplication.submitted_at.desc()).limit(500).all()

    counts = {s: 0 for s in JobApplication.STATUSES}
    for status_key, count in db.session.query(
        JobApplication.status, db.func.count(JobApplication.id),
    ).group_by(JobApplication.status).all():
        counts[status_key] = count

    per_job = {}
    for slug, count in db.session.query(
        JobApplication.job_slug, db.func.count(JobApplication.id),
    ).group_by(JobApplication.job_slug).all():
        per_job[slug] = count

    return jsonify({
        'applications': [a.to_dict() for a in rows],
        'counts':       counts,
        'per_job':      per_job,
        'total':        sum(counts.values()),
        'jobs':         [{'slug': j['slug'], 'title': j['title']} for j in careers_service.list_jobs()],
    }), 200


@careers_bp.route('/api/admin/careers/applications/<app_id>', methods=['GET'])
@login_required
@admin_required
def get_application(app_id):
    application = JobApplication.query.get_or_404(app_id)
    data = application.to_dict(include_text=True)
    data['previewable'] = application.is_previewable
    data['file_exists'] = os.path.exists(application.resume_path or '')
    return jsonify(data), 200


@careers_bp.route('/api/admin/careers/applications/<app_id>', methods=['PATCH'])
@login_required
@admin_required
def update_application(app_id):
    application = JobApplication.query.get_or_404(app_id)
    data = request.get_json(silent=True) or {}

    if 'status' in data:
        if data['status'] not in JobApplication.STATUSES:
            return jsonify({'error': f'status must be one of: {", ".join(JobApplication.STATUSES)}'}), 400
        application.status = data['status']
        application.reviewed_at = datetime.utcnow()
        application.reviewed_by = current_user.id
    if 'admin_note' in data:
        application.admin_note = (data['admin_note'] or '')[:1000] or None

    AuditLog.log('job_application_updated', user_id=current_user.id, resource_id=application.id,
                 metadata={'status': application.status})
    db.session.commit()
    return jsonify(application.to_dict()), 200


@careers_bp.route('/api/admin/careers/applications/<app_id>/resume', methods=['GET'])
@login_required
@admin_required
def download_resume(app_id):
    """Serve the uploaded file. Inline by default so a PDF previews in an iframe;
    ?download=1 forces the save dialog (the only option for Word)."""
    application = JobApplication.query.get_or_404(app_id)
    path = application.resume_path or ''
    if not path or not os.path.exists(path):
        abort(404)

    as_attachment = request.args.get('download') == '1' or application.resume_type != 'pdf'
    return send_file(
        path,
        mimetype=careers_service.resume_mimetype(application.resume_type),
        as_attachment=as_attachment,
        download_name=application.resume_filename,
        max_age=0,
    )


@careers_bp.route('/api/admin/careers/applications/<app_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_application(app_id):
    application = JobApplication.query.get_or_404(app_id)
    path = application.resume_path
    db.session.delete(application)
    AuditLog.log('job_application_deleted', user_id=current_user.id, resource_id=app_id)
    db.session.commit()
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning('Careers: could not delete resume file %s: %s', path, exc)
    return jsonify({'ok': True}), 200
