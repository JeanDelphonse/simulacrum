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

import io
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


def _store(venture, kind: str, text: str, user, filename=None, file_path=None, encrypted: bool = False,
           plain_chars: int = None):
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
                          char_count=plain_chars if plain_chars is not None else len(text or ''),
                          uploaded_by=getattr(user, 'id', None), encrypted=bool(encrypted),
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


def delete_kind(venture, kind: str, user) -> int:
    """Delete an uploaded document: every version of `kind`, its stored files
    and its cached evaluation answers. Past reports keep their quoted passages
    (findings lose only the link to the deleted file). Refused while an
    evaluation is running, since the run reads these files. Commits; returns
    the number of versions removed."""
    from app.extensions import db
    from app.models.founder import EvaluationFinding, FounderEvalCache, FounderVenture, VentureArtifact
    from app.services import founder_evaluation_service as fes
    from app.services import founder_ventures
    if kind not in UPLOAD_KINDS:
        raise IntakeError('That document type cannot be deleted here.')
    if fes.running_run(venture) is not None:
        raise IntakeError('An evaluation is reading your documents right now. Delete it once the evaluation finishes.',
                          409)
    db.session.query(FounderVenture).filter(FounderVenture.id == venture.id).with_for_update().first()
    rows = VentureArtifact.query.filter_by(venture_id=venture.id, kind=kind).all()
    if not rows:
        raise IntakeError('There is no %s to delete.' % KIND_LABELS[kind].lower(), 404)
    ids = [a.id for a in rows]
    root = files_root()
    paths = [a.file_path for a in rows if a.file_path and _inside(a.file_path, root)]
    FounderEvalCache.query.filter(FounderEvalCache.artifact_id.in_(ids)).delete(synchronize_session=False)
    (EvaluationFinding.query.filter(EvaluationFinding.artifact_id.in_(ids))
     .update({'artifact_id': None}, synchronize_session=False))
    VentureArtifact.query.filter(VentureArtifact.id.in_(ids)).delete(synchronize_session=False)
    venture.updated_at = datetime.utcnow()
    founder_ventures.log_event(venture.id, 'artifact_deleted', '%s (%d version%s)' % (
        kind, len(ids), '' if len(ids) == 1 else 's'), user_id=getattr(user, 'id', None))
    db.session.commit()
    for p in paths:
        _remove(p)
    return len(ids)


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


# ---------------------------------------------------------------------------
# Concierge uploads: encrypted at rest (FOUNDER_CONCIERGE_PLAN.md 3.5, D2, D3)
# ---------------------------------------------------------------------------
# An admin uploads a founder's documents for a concierge venture. The original
# file is stored Fernet-encrypted (ENCRYPTION_KEY) and so is the extracted text
# in venture_artifact.text_content (encrypted=1). The plain file never touches
# the disk: checks and text extraction run in memory. Without ENCRYPTION_KEY
# the upload is refused (no plain-text fallback). There is no antivirus on the
# host, so "content checks" are structural: PDF/DOCX only, no macros, OLE or
# ActiveX parts, no external template, no encrypted PDF, no PDF JavaScript,
# launch actions or embedded files. Originals are only ever served as
# attachments. Self-serve uploads (save_upload above) are unchanged.

_PDF_ACTIVE = re.compile(rb'/(JavaScript|JS|Launch|EmbeddedFiles?)(?![A-Za-z])')
_PDF_ACTIVE_STR = re.compile(r'/(JavaScript|JS|Launch|EmbeddedFiles?)(?![A-Za-z])')
CONTENT_CHECK_MESSAGE = ('This file contains macros, embedded files, scripts or encryption, which concierge uploads '
                         'do not accept. Save it again as a plain PDF or .docx (export it, without macros or '
                         'a password) and retry.')


class EncryptionUnavailable(IntakeError):
    """ENCRYPTION_KEY is not configured: concierge files cannot be stored or read."""

    def __init__(self, message: str = 'Encrypted storage is not configured (ENCRYPTION_KEY is missing), so '
                                      'concierge documents cannot be stored or read.'):
        super().__init__(message, 503)


def _fernet():
    from cryptography.fernet import Fernet
    from flask import current_app
    key = current_app.config.get('ENCRYPTION_KEY')
    if not key:
        raise EncryptionUnavailable()
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        raise EncryptionUnavailable('ENCRYPTION_KEY is not a valid Fernet key, so concierge documents cannot be '
                                    'stored or read.')


def encryption_ready() -> bool:
    try:
        _fernet()
        return True
    except EncryptionUnavailable:
        return False


def artifact_text(a) -> str:
    """An artifact's extracted text, decrypted when the artifact is encrypted.
    Every reader of venture_artifact.text_content goes through this.
    Raises EncryptionUnavailable if an encrypted text cannot be decrypted."""
    if a is None:
        return ''
    raw = a.text_content or ''
    if not getattr(a, 'encrypted', False) or not raw:
        return raw
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(raw.encode()).decode('utf-8')
    except InvalidToken:
        logger.error('concierge artifact %s could not be decrypted with the configured key', getattr(a, 'id', '?'))
        raise EncryptionUnavailable('A concierge document could not be decrypted with the configured '
                                    'ENCRYPTION_KEY.')


def artifact_file_bytes(a) -> bytes:
    """The original file of an artifact, decrypted when encrypted. For the
    concierge admin download (served as an attachment only)."""
    if a is None or not a.file_path or not _inside(a.file_path, files_root()):
        raise IntakeError('The file is not available.', 404)
    try:
        with open(a.file_path, 'rb') as fh:
            data = fh.read()
    except OSError:
        raise IntakeError('The file is not available.', 404)
    if not getattr(a, 'encrypted', False):
        return data
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(data)
    except InvalidToken:
        raise EncryptionUnavailable('The file could not be decrypted with the configured ENCRYPTION_KEY.')


def cc_allowed_types() -> list:
    from app.services import founder_config
    return [t for t in founder_config.get('fnd_cc_allowed_types') if t in EXTENSIONS]


def cc_max_upload_bytes() -> int:
    from app.services import founder_config
    return min(int(founder_config.get('fnd_cc_max_upload_mb')) * 1024 * 1024, max_upload_bytes())


def _sniff_bytes(data: bytes, ext: str) -> bool:
    """The same content sniffing as _content_ok, on bytes in memory."""
    try:
        if ext == 'pdf':
            return b'%PDF-' in data[:1024]
        if not data.startswith(b'PK'):
            return False
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            return ('word/document.xml' in names and len(infos) <= DOCX_MAX_ENTRIES
                    and sum(i.file_size for i in infos) <= DOCX_UNPACKED_CAP_BYTES)
    except Exception:
        return False


def structure_problem(data: bytes, ext: str):
    """Why a PDF/DOCX fails the structural content checks, or None."""
    if ext == 'docx':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                for n in names:
                    low = n.lower()
                    if low.endswith('vbaproject.bin') or low.endswith('vbadata.xml'):
                        return 'macros'
                    if '/embeddings/' in low or 'oleobject' in low or '/activex/' in low:
                        return 'embedded objects'
                ct = zf.read('[Content_Types].xml').decode('utf-8', 'replace') if '[Content_Types].xml' in names else ''
                if 'macroenabled' in ct.lower() or 'vbaproject' in ct.lower():
                    return 'macros'
                for n in names:
                    if n.lower().endswith('.rels'):
                        rels = zf.read(n).decode('utf-8', 'replace')
                        if 'attachedTemplate' in rels and 'External' in rels:
                            return 'external template'
        except Exception:
            return 'unreadable'
        return None
    if ext == 'pdf':
        if _PDF_ACTIVE.search(data):
            return 'scripts or embedded files'
        try:
            import fitz
            with fitz.open(stream=data, filetype='pdf') as doc:
                if doc.needs_pass or doc.is_encrypted or (doc.metadata or {}).get('encryption') \
                        or re.search(rb'/Encrypt(?![A-Za-z])', data):
                    return 'encrypted'
                if doc.embfile_count() > 0:
                    return 'embedded files'
                for x in range(1, doc.xref_length()):     # objects inside compressed streams too
                    try:
                        obj = doc.xref_object(x, compressed=False)
                    except Exception:
                        continue
                    if obj and _PDF_ACTIVE_STR.search(obj):
                        return 'scripts or embedded files'
        except Exception:
            return 'unreadable'
        return None
    return 'type'


def _extract_bytes(data: bytes, ext: str) -> str:
    """resume_parser's PDF/DOCX extraction, in memory."""
    if ext == 'pdf':
        import fitz
        with fitz.open(stream=data, filetype='pdf') as doc:
            return '\n'.join(page.get_text() for page in doc).strip()
    from docx import Document
    doc = Document(io.BytesIO(data))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    paragraphs.append(cell.text.strip())
    return '\n'.join(paragraphs).strip()


def save_concierge_upload(venture, file_storage, kind: str, user):
    """Store one concierge document encrypted, as the new current version of
    `kind`. Types and size come from the Concierge settings. Raises IntakeError
    (EncryptionUnavailable when ENCRYPTION_KEY is missing)."""
    from werkzeug.utils import secure_filename
    from app.services.careers_service import upload_size
    from utils.id_gen import generate_id

    fernet = _fernet()                    # refuse before reading anything
    if kind not in UPLOAD_KINDS:
        raise IntakeError('Choose which document this is.')
    if file_storage is None or not getattr(file_storage, 'filename', ''):
        raise IntakeError('Choose a file to upload.')
    original = file_storage.filename
    ext = original.rsplit('.', 1)[1].lower() if '.' in original else ''
    allowed = cc_allowed_types()
    if ext not in allowed:
        raise IntakeError('Only %s files are accepted.' % ' and '.join(t.upper() for t in allowed))
    cap = cc_max_upload_bytes()
    size = upload_size(file_storage)
    if size > cap:
        raise IntakeError('The file must be %d MB or smaller.' % (cap // (1024 * 1024)), 413)
    if size == 0:
        raise IntakeError('That file is empty.')
    data = file_storage.stream.read(cap + 1)
    if len(data) > cap:
        raise IntakeError('The file must be %d MB or smaller.' % (cap // (1024 * 1024)), 413)
    if not _sniff_bytes(data, ext):
        raise IntakeError("That file isn't a readable %s. Save it again as PDF or .docx and retry."
                          % ('PDF' if ext == 'pdf' else 'Word (.docx) document'), 422)
    problem = structure_problem(data, ext)
    if problem:
        logger.warning('concierge upload refused for venture %s: %s', venture.id, problem)
        raise IntakeError(CONTENT_CHECK_MESSAGE, 422)
    try:
        text = _extract_bytes(data, ext)
    except Exception as exc:
        logger.warning('concierge upload parse failed for venture %s: %s', venture.id, exc)
        raise IntakeError("We couldn't read that file. Save it again as PDF or .docx and retry.", 422)
    if _readable_chars(text) < _unreadable_threshold():
        raise IntakeError('We could read almost no text from this file. It may be a scanned image. '
                          'Upload a text-based PDF or Word document.', 422)
    safe = secure_filename(original) or ('document.' + ext)
    if not safe.lower().endswith('.' + ext):
        safe = '%s.%s' % (safe, ext)
    path = os.path.join(_venture_dir(venture), '%s_%s.enc' % (generate_id(), safe))
    with open(path, 'wb') as fh:
        fh.write(fernet.encrypt(data))
    try:
        return _store(venture, kind, fernet.encrypt(text.encode('utf-8')).decode('ascii'), user, filename=safe,
                      file_path=path, encrypted=True, plain_chars=len(text))
    except Exception:
        _remove(path)
        raise
