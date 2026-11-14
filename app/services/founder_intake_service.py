"""Founder Track intake (FOUNDER_PLAN.md WP1): uploaded documents and pitch
transcripts become versioned venture_artifact rows.

    save_upload(venture, file_storage, kind, user) -> VentureArtifact
    add_transcript(venture, text, user)            -> VentureArtifact

Uploads are PDF or DOCX only, checked by content (PDF header; DOCX is a zip
holding word/document.xml under an unpacked-size cap) before resume_parser
extracts the text. A file that yields almost no text (a scanned image) is
refused the way resume uploads are.

Files are stored under FOUNDER_FILES_FOLDER/<venture_id>/. That folder is
deliberately NOT inside UPLOAD_FOLDER: UPLOAD_FOLDER is served publicly by
/avatars/<path>. The default is a 'founder_private' folder beside
UPLOAD_FOLDER; config or the FOUNDER_FILES_FOLDER environment variable can
move it, but never into UPLOAD_FOLDER.
Each kind keeps every version; the newest one is is_current. Nothing here
writes to the resumes table: guide facts and bio readiness read it.
"""
from __future__ import annotations

import logging
import os
import re
import zipfile
from datetime import datetime

logger = logging.getLogger(__name__)

UPLOAD_KINDS = ('startup_plan', 'exec_summary', 'ops_plan', 'pitch_deck', 'financial_model')
TRANSCRIPT_KIND = 'pitch_transcript'
ALL_KINDS = UPLOAD_KINDS + (TRANSCRIPT_KIND,)
KIND_LABELS = {
    'startup_plan': 'Startup plan',
    'exec_summary': 'Executive summary',
    'ops_plan': 'Operations plan',
    'pitch_deck': 'Pitch deck',
    'financial_model': 'Financial model',
    'pitch_transcript': 'Pitch transcript',
}
KIND_HINTS = {
    'startup_plan': 'The full business or startup plan.',
    'exec_summary': 'One or two pages: problem, customer, solution, ask.',
    'ops_plan': 'How the work gets done: people, suppliers, delivery.',
    'pitch_deck': 'Your slides, exported as PDF.',
    'financial_model': 'Export the model or its summary sheet as PDF or Word.',
    'pitch_transcript': 'Created when you confirm a spoken or typed pitch below.',
}
EXTENSIONS = ('pdf', 'docx')
UPLOAD_CAP_BYTES = 20 * 1024 * 1024     # the per-route ceiling (owner decision 2026-11-13); config can only lower it
TRANSCRIPT_MAX_CHARS = 60000
DOCX_UNPACKED_CAP_BYTES = 100 * 1024 * 1024   # refuse zip bombs before python-docx unpacks them
DOCX_MAX_ENTRIES = 2000


class IntakeError(ValueError):
    """User-facing refusal. status is the HTTP code the route should return."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def max_upload_bytes() -> int:
    """The founder upload ceiling: 20 MB, or the app's MAX_CONTENT_LENGTH if lower."""
    try:
        from flask import current_app
        app_cap = int(current_app.config.get('MAX_CONTENT_LENGTH') or UPLOAD_CAP_BYTES)
    except Exception:
        app_cap = UPLOAD_CAP_BYTES
    return min(UPLOAD_CAP_BYTES, app_cap)


def _unreadable_threshold() -> int:
    """Characters below which an upload is treated as a scanned image (shared
    with resume uploads: bio_readiness 'bio_ready_unreadable_chars')."""
    try:
        from app.services import bio_readiness
        return int(bio_readiness.setting('bio_ready_unreadable_chars'))
    except Exception:
        return 200


def _readable_chars(text: str) -> int:
    return len(re.sub(r'\s+', ' ', text or '').strip())


def _inside(path: str, root: str) -> bool:
    try:
        path, root = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([path, root]) == root
    except ValueError:          # different drives on Windows
        return False


def files_root() -> str:
    """Where founder files live: never inside the publicly served UPLOAD_FOLDER."""
    from flask import current_app
    upload = current_app.config['UPLOAD_FOLDER']
    default = os.path.join(os.path.dirname(os.path.normpath(upload)), 'founder_private')
    root = current_app.config.get('FOUNDER_FILES_FOLDER') or os.environ.get('FOUNDER_FILES_FOLDER') or default
    if _inside(root, upload):
        logger.error('FOUNDER_FILES_FOLDER %s is inside the public UPLOAD_FOLDER; using %s instead', root, default)
        root = default
    return root


def _venture_dir(venture) -> str:
    folder = os.path.join(files_root(), venture.id)
    os.makedirs(folder, exist_ok=True)
    return folder


def venture_dir(venture) -> str:
    """The venture's private upload folder (also used for the pitch draft)."""
    return _venture_dir(venture)


def _next_version(venture_id: str, kind: str) -> int:
    from sqlalchemy import func
    from app.extensions import db
    from app.models.founder import VentureArtifact
    top = (db.session.query(func.max(VentureArtifact.version))
           .filter(VentureArtifact.venture_id == venture_id, VentureArtifact.kind == kind).scalar())
    return int(top or 0) + 1


def _store(venture, kind: str, text: str, user, filename=None, file_path=None):
    """Retire the current version of `kind` and add the new one. Commits.

    The venture row is locked first (SELECT ... FOR UPDATE; a no-op on SQLite)
    so two uploads of the same kind at once get distinct versions and only the
    newest stays current. `filename` may contain {version}."""
    from app.extensions import db
    from app.models.founder import FounderVenture, VentureArtifact
    from app.services import founder_ventures
    db.session.query(FounderVenture).filter(FounderVenture.id == venture.id).with_for_update().first()
    version = _next_version(venture.id, kind)
    (VentureArtifact.query.filter_by(venture_id=venture.id, kind=kind, is_current=True)
     .update({'is_current': False}, synchronize_session=False))
    art = VentureArtifact(venture_id=venture.id, kind=kind, version=version,
                          is_current=True, filename=(filename or '').replace('{version}', str(version)) or None,
                          file_path=file_path, text_content=text,
                          char_count=len(text or ''), uploaded_by=getattr(user, 'id', None),
                          created_at=datetime.utcnow())
    db.session.add(art)
    db.session.flush()
    venture.updated_at = datetime.utcnow()
    founder_ventures.log_event(venture.id, 'artifact_added', '%s v%d' % (kind, art.version),
                               user_id=getattr(user, 'id', None))
    db.session.commit()
    return art


def _content_ok(path: str, ext: str) -> bool:
    """Sniff the saved file: a PDF must carry the %PDF- header; a DOCX must be
    a zip holding word/document.xml whose unpacked size stays under the cap."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(1024)
        if ext == 'pdf':
            return b'%PDF-' in head
        if not head.startswith(b'PK') or not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            return ('word/document.xml' in names and len(infos) <= DOCX_MAX_ENTRIES
                    and sum(i.file_size for i in infos) <= DOCX_UNPACKED_CAP_BYTES)
    except Exception:
        return False


def save_upload(venture, file_storage, kind: str, user):
    """Save one PDF/DOCX as the new current version of `kind`. Raises IntakeError."""
    from werkzeug.utils import secure_filename
    from app.services.careers_service import upload_size
    from app.services.resume_parser import parse_resume
    from utils.id_gen import generate_id

    if kind not in UPLOAD_KINDS:
        raise IntakeError('Choose which document this is.')
    if file_storage is None or not getattr(file_storage, 'filename', ''):
        raise IntakeError('Choose a file to upload.')
    original = file_storage.filename
    ext = original.rsplit('.', 1)[1].lower() if '.' in original else ''
    if ext not in EXTENSIONS:
        raise IntakeError('Only PDF and Word (.docx) files are accepted.')
    cap = max_upload_bytes()
    size = upload_size(file_storage)
    if size > cap:
        raise IntakeError('The file must be %d MB or smaller.' % (cap // (1024 * 1024)), 413)
    if size == 0:
        raise IntakeError('That file is empty.')

    safe = secure_filename(original) or ('document.' + ext)
    if not safe.lower().endswith('.' + ext):
        safe = '%s.%s' % (safe, ext)
    path = os.path.join(_venture_dir(venture), '%s_%s' % (generate_id(), safe))
    file_storage.save(path)
    if not _content_ok(path, ext):
        _remove(path)
        raise IntakeError("That file isn't a readable %s. Save it again as PDF or .docx and retry."
                          % ('PDF' if ext == 'pdf' else 'Word (.docx) document'), 422)
    try:
        text = parse_resume(path, ext)
    except Exception as exc:
        logger.warning('founder upload parse failed for venture %s: %s', venture.id, exc)
        _remove(path)
        raise IntakeError("We couldn't read that file. Save it again as PDF or .docx and retry.", 422)
    if _readable_chars(text) < _unreadable_threshold():
        _remove(path)
        raise IntakeError('We could read almost no text from this file. It may be a scanned image. '
                          'Upload a text-based PDF or Word document (export it rather than scanning it).', 422)
    try:
        return _store(venture, kind, text, user, filename=safe, file_path=path)
    except Exception:
        _remove(path)
        raise


def add_transcript(venture, text: str, user):
    """Save a confirmed pitch transcript as the new current pitch_transcript."""
    text = (text or '').strip()
    if not text:
        raise IntakeError('The transcript is empty.')
    if len(text) > TRANSCRIPT_MAX_CHARS:
        text = text[:TRANSCRIPT_MAX_CHARS]
    return _store(venture, TRANSCRIPT_KIND, text, user, filename='pitch_transcript_v{version}.txt')


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def artifacts(venture, current_only: bool = False) -> list:
    from app.models.founder import VentureArtifact
    q = VentureArtifact.query.filter_by(venture_id=venture.id)
    if current_only:
        q = q.filter_by(is_current=True)
    return q.order_by(VentureArtifact.kind, VentureArtifact.version.desc()).all()


def summary(venture) -> list:
    """One row per kind: the current version (or None, a gap) and how many
    versions exist. Missing kinds stay None, never zero-filled."""
    rows = {k: {'kind': k, 'label': KIND_LABELS[k], 'hint': KIND_HINTS[k], 'uploadable': k in UPLOAD_KINDS,
                'current': None, 'versions': 0} for k in ALL_KINDS}
    for a in artifacts(venture):
        r = rows.get(a.kind)
        if r is None:
            continue
        r['versions'] += 1
        if a.is_current:
            r['current'] = a.to_dict()
    return [rows[k] for k in ALL_KINDS]
