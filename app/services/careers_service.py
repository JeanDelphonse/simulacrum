"""Careers — role definitions and application intake.

Roles are static content transcribed from docs/Simulacrum_Team_Job_Specs.docx into
config/jobs.json, so editing a posting is a config change, not a migration. Only
the applications are stored in the database.
"""
from __future__ import annotations
import logging
import os

logger = logging.getLogger(__name__)

_JOBS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'config', 'jobs.json',
)

# Word is offered because most applicants send .docx; .doc is accepted so an older
# resume is not silently rejected, but it cannot be previewed or text-extracted.
# The 5 MB size ceiling is Flask's MAX_CONTENT_LENGTH, enforced before the request
# body reaches the route — deliberately not duplicated here.
ALLOWED_RESUME_EXTENSIONS = {'pdf', 'docx', 'doc'}

_cache = {'data': None}


def _load() -> dict:
    import json
    with open(_JOBS_PATH, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def _data() -> dict:
    if _cache['data'] is None:
        _cache['data'] = _load()
    return _cache['data']


def careers_meta() -> dict:
    """Shared copy that applies to every role (about, location, team goal)."""
    return _data().get('meta', {})


def list_jobs() -> list:
    return _data().get('jobs', [])


def get_job(slug: str):
    for job in list_jobs():
        if job['slug'] == slug:
            return job
    return None


def job_title(slug: str) -> str:
    job = get_job(slug)
    return job['title'] if job else slug


def resume_extension(filename: str):
    """Lowercased extension if it is an accepted resume format, else None."""
    if not filename or '.' not in filename:
        return None
    ext = filename.rsplit('.', 1)[1].lower()
    return ext if ext in ALLOWED_RESUME_EXTENSIONS else None


def resume_upload_dir() -> str:
    """Resume uploads live in their own subfolder so they never mix with the
    resumes users upload for simulations."""
    from flask import current_app
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'careers')
    os.makedirs(path, exist_ok=True)
    return path


def extract_resume_text(file_path: str, ext: str) -> str:
    """Best-effort text extraction for the admin preview. Never raises: a resume
    we cannot read is still a valid application, it just previews as a download."""
    try:
        if ext in ('pdf', 'docx'):
            from app.services.resume_parser import parse_resume
            return parse_resume(file_path, ext)
    except Exception as exc:
        logger.warning('Careers: could not extract text from %s: %s', file_path, exc)
    return ''


def resume_mimetype(ext: str) -> str:
    return {
        'pdf':  'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'doc':  'application/msword',
    }.get(ext, 'application/octet-stream')
