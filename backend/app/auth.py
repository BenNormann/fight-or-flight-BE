"""Sign-in plumbing shared by the auth endpoints: the Google OAuth client, the FE origin
allow-list, `next` validation and the session user.

The session is Flask's signed cookie (httpOnly). It holds only the signed-in user's public
profile, keyed by Google's stable `sub`; nothing is stored server-side.
"""
from urllib.parse import urlencode, urlsplit

from authlib.integrations.flask_client import OAuth
from flask import current_app, session

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
SESSION_USER = "user"
SESSION_NEXT = "auth_next"


def init_oauth(app):
    """One OAuth registry per app, so tests (and multiple apps) never share a client."""
    oauth = OAuth(app)
    app.extensions["oauth_google"] = oauth.register(
        name="google",
        server_metadata_url=GOOGLE_METADATA_URL,
        client_kwargs={"scope": "openid email profile"},
    )


def google_client():
    return current_app.extensions["oauth_google"]


def frontend_origins(app=None):
    raw = (app or current_app).config["FRONTEND_ORIGINS"]
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def oauth_configured():
    c = current_app.config
    return bool(c["SECRET_KEY"] and c["GOOGLE_CLIENT_ID"] and c["GOOGLE_CLIENT_SECRET"])


def _origin_of(url):
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def safe_next(raw):
    """`raw` if it is an absolute http(s) URL on an allow-listed FE origin, else the first FE
    origin's root. Guards the post-sign-in redirect against open redirects."""
    origins = frontend_origins()
    fallback = f"{origins[0]}/" if origins else "/"
    if not raw or any(ch.isspace() or ord(ch) < 32 for ch in raw) or "\\" in raw:
        return fallback
    try:
        u = urlsplit(raw)
    except ValueError:
        return fallback
    if u.scheme not in ("http", "https") or not u.netloc or "@" in u.netloc:
        return fallback
    if _origin_of(raw) not in {o.lower() for o in origins}:
        return fallback
    return raw


def login_error_url(code, next_url):
    """The FE's `/login?error=<code>&next=<path>` on the same origin as `next_url`."""
    u = urlsplit(next_url)
    if not u.netloc:  # no FE origin configured
        return f"/login?{urlencode({'error': code})}"
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return f"{u.scheme}://{u.netloc}/login?{urlencode({'error': code, 'next': path})}"


def email_allowed(email):
    domain = current_app.config["AUTH_ALLOWED_DOMAIN"].lower()
    local, sep, host = (email or "").strip().lower().rpartition("@")
    return bool(sep and local and host == domain)


def userinfo_allowed(info):
    """Google says the email is verified, it is in the allowed domain, and the account is managed
    by that domain's Workspace (`hd`), not a consumer account registered with a USC address."""
    domain = current_app.config["AUTH_ALLOWED_DOMAIN"].lower()
    return bool(info.get("email_verified")) and email_allowed(info.get("email")) and (info.get("hd") or "").lower() == domain


def start_session(user):
    """Fresh session for `user` (drops OAuth state and any pre-login session: no fixation)."""
    session.clear()
    session.permanent = True
    session[SESSION_USER] = user


def current_user():
    return session.get(SESSION_USER)
