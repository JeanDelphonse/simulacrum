"""Shared helpers for founder routes."""
from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user


def founder_required(view=None, key: str = 'founder_track'):
    """404 unless the Founder Track (or the given sub-switch) is enabled for
    the current user. Anonymous users get 404 too, so the track stays hidden."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from app.services import founder_feature
            if not founder_feature.is_enabled(current_user, key):
                abort(404)
            return fn(*args, **kwargs)
        return wrapper
    return deco(view) if view is not None else deco
