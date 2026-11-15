"""Google sign-in limited to USC accounts, and the cookie session the FE asks `/me` about.

Flow: the FE sends the browser to `/login?next=<FE url>` → Google → `/callback`, which sets the
session cookie and redirects to `next`. A refused or failed sign-in redirects to the FE's
`/login?error=domain|cancelled|failed|unavailable`.
"""
import logging

import requests
from authlib.integrations.base_client.errors import OAuthError
from flask import current_app, redirect, request, session, url_for
from flask_smorest import Blueprint, abort

from ..auth import (SESSION_NEXT, current_user, email_allowed, google_client, login_error_url, oauth_configured,
                    safe_next, start_session, userinfo_allowed)

log = logging.getLogger(__name__)

blp = Blueprint("auth", __name__, url_prefix="/api/v1/auth",
                description="Sign-in with Google (USC accounts only) and the session")


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@blp.route("/login")
def login():
    """Full-page redirect to Google. `next` = absolute FE URL to return to (allow-listed origins only)."""
    nxt = safe_next(request.args.get("next"))
    if not oauth_configured():
        log.warning("sign-in requested but SECRET_KEY / GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
        return _no_store(redirect(login_error_url("unavailable", nxt)))
    session[SESSION_NEXT] = nxt
    redirect_uri = current_app.config["OAUTH_REDIRECT_URI"] or url_for("auth.callback", _external=True)
    # `hd` makes Google offer USC accounts first; the callback still enforces the domain.
    resp = google_client().authorize_redirect(
        redirect_uri, hd=current_app.config["AUTH_ALLOWED_DOMAIN"], prompt="select_account")
    return _no_store(resp)


@blp.route("/callback")
def callback():
    """Google returns here. Creates the session and redirects to `next`."""
    nxt = safe_next(session.pop(SESSION_NEXT, None))
    if not oauth_configured():
        return _no_store(redirect(login_error_url("unavailable", nxt)))
    error = request.args.get("error")
    if error:
        return _no_store(redirect(login_error_url("cancelled" if error == "access_denied" else "failed", nxt)))
    try:
        token = google_client().authorize_access_token()  # checks state, verifies the ID token
    except (OAuthError, requests.RequestException, ValueError) as exc:
        log.warning("sign-in callback failed: %s", exc)
        return _no_store(redirect(login_error_url("failed", nxt)))
    info = token.get("userinfo") or {}
    if not info.get("sub"):
        return _no_store(redirect(login_error_url("failed", nxt)))
    if not userinfo_allowed(info):
        return _no_store(redirect(login_error_url("domain", nxt)))
    start_session({
        "id": info["sub"],
        "email": info["email"].strip().lower(),
        "name": info.get("name"),
        "picture": info.get("picture"),
    })
    return _no_store(redirect(nxt))


@blp.route("/me")
@blp.response(200)
def me():
    """`{id, email, name, picture}` for the signed-in user; 401 when signed out."""
    user = current_user()
    if not user:
        abort(401, message="Not signed in")
    return user, 200, {"Cache-Control": "no-store"}


@blp.route("/logout", methods=["POST"])
@blp.response(204)
def logout():
    """Clears the session cookie."""
    session.clear()
    return ""


@blp.route("/dev/login")
def dev_login():
    """Local testing only (`AUTH_DEV_LOGIN=true`): sign in as `?email=` without Google. 404 otherwise."""
    if not current_app.config["AUTH_DEV_LOGIN"]:
        abort(404)
    nxt = safe_next(request.args.get("next"))
    if not current_app.config["SECRET_KEY"]:
        return _no_store(redirect(login_error_url("unavailable", nxt)))
    email = (request.args.get("email") or "").strip().lower()
    if not email_allowed(email):
        return _no_store(redirect(login_error_url("domain", nxt)))
    start_session({"id": f"dev:{email}", "email": email, "name": email.split("@")[0], "picture": None})
    return _no_store(redirect(nxt))
