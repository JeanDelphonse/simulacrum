import os
import secrets
from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.blueprints.resumes import resumes_bp
from app.extensions import db
from app.models.resume import Resume
from app.models.audit_log import AuditLog
from app.services.resume_parser import parse_resume, allowed_file
from app.services.claude import extract_expertise_zones, normalize_linkedin_text
from app.services.linkedin import get_auth_url, exchange_code_for_token, crawl_profile, encrypt_token
from utils.id_gen import generate_id


@resumes_bp.route('', methods=['GET'])
@login_required
def list_resumes():
    resumes = Resume.query.filter_by(user_id=current_user.id).order_by(Resume.created_at.desc()).all()
    return jsonify([{
        'id': r.id,
        'label': r.label,
        'source': r.source,
        'file_type': r.file_type,
        'has_expertise_zones': r.expertise_zones is not None,
        'created_at': r.created_at.isoformat(),
    } for r in resumes]), 200


@resumes_bp.route('/upload', methods=['POST'])
@login_required
def upload_resume():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename, current_app.config['ALLOWED_EXTENSIONS']):
        return jsonify({'error': 'Only PDF and DOCX files are accepted'}), 400

    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower()
    unique_filename = f"{generate_id()}_{filename}"
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, unique_filename)
    file.save(file_path)

    try:
        parsed_text = parse_resume(file_path, ext)
    except Exception as e:
        os.remove(file_path)
        return jsonify({'error': f'Failed to parse file: {str(e)}'}), 422

    label = request.form.get('label', filename)
    resume = Resume(
        id=generate_id(),
        user_id=current_user.id,
        label=label,
        file_path=file_path,
        file_type=ext,
        source='upload',
        parsed_text=parsed_text,
    )
    db.session.add(resume)
    AuditLog.log('resume_uploaded', user_id=current_user.id, resource_id=resume.id)
    db.session.commit()

    return jsonify({
        'id': resume.id,
        'label': resume.label,
        'parsed_text_preview': parsed_text[:500],
        'message': 'Resume uploaded and parsed successfully',
    }), 201


@resumes_bp.route('/linkedin', methods=['GET'])
@login_required
def linkedin_oauth_start():
    state = secrets.token_urlsafe(16)
    from flask import session
    session['linkedin_oauth_state'] = state
    auth_url = get_auth_url(state)
    return jsonify({'auth_url': auth_url}), 200


@resumes_bp.route('/linkedin/callback', methods=['GET'])
@login_required
def linkedin_oauth_callback():
    from flask import session, redirect, url_for
    code = request.args.get('code')
    state = request.args.get('state')

    if not code:
        return jsonify({'error': 'LinkedIn OAuth cancelled or failed'}), 400

    try:
        token_data = exchange_code_for_token(code)
        access_token = token_data.get('access_token')
        raw_profile = crawl_profile(access_token)
        normalized_text = normalize_linkedin_text(raw_profile, current_user.id)

        resume = Resume(
            id=generate_id(),
            user_id=current_user.id,
            label='LinkedIn Profile',
            source='linkedin',
            parsed_text=normalized_text,
            linkedin_access_token_enc=encrypt_token(access_token),
        )
        db.session.add(resume)
        AuditLog.log('linkedin_imported', user_id=current_user.id, resource_id=resume.id)
        db.session.commit()

        return redirect(url_for('pages.resume_detail', resume_id=resume.id))
    except Exception as e:
        return jsonify({'error': f'LinkedIn import failed: {str(e)}'}), 500


@resumes_bp.route('/<resume_id>', methods=['GET'])
@login_required
def get_resume(resume_id):
    resume = Resume.query.filter_by(id=resume_id, user_id=current_user.id).first_or_404()
    return jsonify({
        'id': resume.id,
        'label': resume.label,
        'source': resume.source,
        'file_type': resume.file_type,
        'parsed_text': resume.parsed_text,
        'expertise_zones': resume.expertise_zones,
        'created_at': resume.created_at.isoformat(),
    }), 200


@resumes_bp.route('/<resume_id>', methods=['PUT'])
@login_required
def update_resume(resume_id):
    resume = Resume.query.filter_by(id=resume_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    if 'parsed_text' in data:
        resume.parsed_text = data['parsed_text']
        resume.expertise_zones = None  # Invalidate cache
    if 'label' in data:
        resume.label = data['label']
    db.session.commit()
    return jsonify({'message': 'Resume updated', 'id': resume.id}), 200


@resumes_bp.route('/<resume_id>/extract-zones', methods=['POST'])
@login_required
def extract_zones(resume_id):
    resume = Resume.query.filter_by(id=resume_id, user_id=current_user.id).first_or_404()
    if not resume.parsed_text:
        return jsonify({'error': 'No parsed text available'}), 400

    try:
        zones = extract_expertise_zones(resume.parsed_text, current_user.id)
        resume.expertise_zones = zones
        db.session.commit()
        return jsonify({'expertise_zones': zones}), 200
    except Exception as e:
        return jsonify({'error': f'Zone extraction failed: {str(e)}'}), 500


@resumes_bp.route('/<resume_id>/linkedin-sync', methods=['POST'])
@login_required
def linkedin_sync(resume_id):
    resume = Resume.query.filter_by(id=resume_id, user_id=current_user.id, source='linkedin').first_or_404()
    if not resume.linkedin_access_token_enc:
        return jsonify({'error': 'No LinkedIn token stored. Please re-authorize LinkedIn.'}), 400

    from app.services.linkedin import decrypt_token
    try:
        access_token = decrypt_token(resume.linkedin_access_token_enc)
        raw_profile = crawl_profile(access_token)
        normalized_text = normalize_linkedin_text(raw_profile, current_user.id)
        resume.parsed_text = normalized_text
        resume.expertise_zones = None  # Invalidate cache
        db.session.commit()
        return jsonify({'message': 'LinkedIn profile re-synced successfully'}), 200
    except Exception as e:
        return jsonify({'error': f'LinkedIn sync failed: {str(e)}'}), 500


@resumes_bp.route('/<resume_id>', methods=['DELETE'])
@login_required
def delete_resume(resume_id):
    resume = Resume.query.filter_by(id=resume_id, user_id=current_user.id).first_or_404()
    if resume.file_path and os.path.exists(resume.file_path):
        os.remove(resume.file_path)
    AuditLog.log('resume_deleted', user_id=current_user.id, resource_id=resume_id)
    db.session.delete(resume)
    db.session.commit()
    return jsonify({'message': 'Resume deleted'}), 200
