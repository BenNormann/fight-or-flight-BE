from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import redirect

from app import create_app

FE = "http://localhost:5173"
SECRET = "test-secret-0123456789-abcdefghijklmnop"  # >= 32 chars, like production
SETTINGS = dict(TESTING=True, SECRET_KEY=SECRET, GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="csecret",
                FRONTEND_ORIGINS=f"{FE},https://fof.example.edu")


class FakeGoogle:
    """Stands in for the Authlib client: records the redirect, returns a canned token."""

    def __init__(self):
        self.userinfo = {"sub": "123", "email": "Tommy@USC.edu", "email_verified": True, "hd": "usc.edu",
                         "name": "Tommy Trojan", "picture": "https://example.com/t.png"}
        self.redirect_args = None
        self.error = None

    def authorize_redirect(self, redirect_uri, **kwargs):
        self.redirect_args = (redirect_uri, kwargs)
        return redirect("https://accounts.google.com/o/oauth2/v2/auth?state=x")

    def authorize_access_token(self):
        if self.error:
            raise self.error
        return {"access_token": "t", "userinfo": self.userinfo}


def make_app(**overrides):
    app = create_app(**{**SETTINGS, **overrides})
    fake = FakeGoogle()
    app.extensions["oauth_google"] = fake
    return app, fake


@pytest.fixture
def setup():
    app, fake = make_app()
    return app.test_client(), fake


def query(resp):
    u = urlsplit(resp.headers["Location"])
    return f"{u.scheme}://{u.netloc}{u.path}", {k: v[0] for k, v in parse_qs(u.query).items()}


def sign_in(client, next_url=f"{FE}/route?to=leavey"):
    client.get("/api/v1/auth/login", query_string={"next": next_url})
    return client.get("/api/v1/auth/callback?code=abc&state=x")


def test_routes_documented(setup):
    client, _ = setup
    paths = set(client.get("/api/openapi.json").get_json()["paths"])
    for p in ("/api/v1/auth/login", "/api/v1/auth/callback", "/api/v1/auth/me", "/api/v1/auth/logout"):
        assert p in paths, p


def test_me_signed_out_is_401(setup):
    client, _ = setup
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_login_redirects_to_google_with_domain_hint(setup):
    client, fake = setup
    resp = client.get("/api/v1/auth/login", query_string={"next": f"{FE}/route"})
    assert resp.status_code == 302 and resp.headers["Location"].startswith("https://accounts.google.com/")
    redirect_uri, kwargs = fake.redirect_args
    assert redirect_uri == "http://localhost/api/v1/auth/callback"
    assert kwargs["hd"] == "usc.edu"


def test_explicit_redirect_uri_wins():
    app, fake = make_app(OAUTH_REDIRECT_URI="https://api.example.edu/api/v1/auth/callback")
    app.test_client().get("/api/v1/auth/login")
    assert fake.redirect_args[0] == "https://api.example.edu/api/v1/auth/callback"


def test_full_sign_in_sets_cookie_session_and_returns_to_next(setup):
    client, _ = setup
    resp = sign_in(client)
    assert resp.status_code == 302
    assert resp.headers["Location"] == f"{FE}/route?to=leavey"
    cookie = resp.headers["Set-Cookie"]
    assert "fof_session=" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.headers["Cache-Control"] == "no-store"
    assert me.get_json() == {"id": "123", "email": "tommy@usc.edu", "name": "Tommy Trojan",
                             "picture": "https://example.com/t.png"}


def test_logout_clears_session(setup):
    client, _ = setup
    sign_in(client)
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("change", [
    {"email": "tommy@gmail.com", "hd": None},
    {"email": "tommy@usc.edu.evil.com", "hd": "usc.edu.evil.com"},
    {"hd": None},                     # consumer Google account registered with a USC address
    {"email_verified": False},
])
def test_non_usc_accounts_refused(setup, change):
    client, fake = setup
    fake.userinfo = {**fake.userinfo, **change}
    resp = sign_in(client)
    url, q = query(resp)
    assert url == f"{FE}/login" and q == {"error": "domain", "next": "/route?to=leavey"}
    assert client.get("/api/v1/auth/me").status_code == 401


def test_cancelled_at_google(setup):
    client, _ = setup
    client.get("/api/v1/auth/login", query_string={"next": f"{FE}/"})
    url, q = query(client.get("/api/v1/auth/callback?error=access_denied"))
    assert url == f"{FE}/login" and q["error"] == "cancelled"


def test_bad_state_fails_cleanly(setup):
    from authlib.integrations.base_client.errors import MismatchingStateError

    client, fake = setup
    fake.error = MismatchingStateError()
    _, q = query(sign_in(client))
    assert q["error"] == "failed"


def test_not_configured_redirects_with_unavailable():
    app, _ = make_app(GOOGLE_CLIENT_ID="")
    resp = app.test_client().get("/api/v1/auth/login", query_string={"next": f"{FE}/route"})
    url, q = query(resp)
    assert url == f"{FE}/login" and q["error"] == "unavailable"


@pytest.mark.parametrize("bad", [
    "https://evil.com/",
    "//evil.com/x",
    "/route",                          # relative: no FE origin to return to
    f"{FE}@evil.com/",
    "javascript:alert(1)",
    "http://localhost:5174/",
    f"{FE}/\\evil.com",
    f"{FE}/x\nSet-Cookie: a=b",
])
def test_next_must_be_on_an_allowed_origin(setup, bad):
    client, _ = setup
    resp = sign_in(client, bad)
    assert resp.headers["Location"] == f"{FE}/"


def test_second_allowed_origin_kept(setup):
    client, _ = setup
    assert sign_in(client, "https://fof.example.edu/route").headers["Location"] == "https://fof.example.edu/route"


def test_cors_names_origin_and_allows_credentials(setup):
    client, _ = setup
    ok = client.get("/api/v1/auth/me", headers={"Origin": FE})
    assert ok.headers["Access-Control-Allow-Origin"] == FE
    assert ok.headers["Access-Control-Allow-Credentials"] == "true"
    other = client.get("/api/v1/auth/me", headers={"Origin": "https://evil.com"})
    assert "Access-Control-Allow-Origin" not in other.headers


def test_cross_site_cookie_settings():
    app, _ = make_app(SESSION_COOKIE_SAMESITE="None", SESSION_COOKIE_SECURE=True)
    cookie = sign_in(app.test_client(), f"{FE}/").headers["Set-Cookie"]
    assert "SameSite=None" in cookie and "Secure" in cookie


def test_dev_login_off_by_default(setup):
    client, _ = setup
    assert client.get("/api/v1/auth/dev/login?email=a@usc.edu").status_code == 404


def test_dev_login_when_enabled():
    app, _ = make_app(AUTH_DEV_LOGIN=True)
    client = app.test_client()
    _, q = query(client.get("/api/v1/auth/dev/login", query_string={"email": "a@gmail.com", "next": f"{FE}/route"}))
    assert q["error"] == "domain"
    resp = client.get("/api/v1/auth/dev/login", query_string={"email": "A@usc.edu", "next": f"{FE}/route"})
    assert resp.headers["Location"] == f"{FE}/route"
    assert client.get("/api/v1/auth/me").get_json()["email"] == "a@usc.edu"


def test_callback_url_is_https_behind_nginx(setup):
    """nginx terminates TLS and forwards plain HTTP; ProxyFix must honour X-Forwarded-Proto/Host,
    or Google rejects the http:// redirect_uri (redirect_uri_mismatch)."""
    client, fake = setup
    client.get("/api/v1/auth/login", headers={
        "Host": "trojan-odyssey.northcentralus.cloudapp.azure.com", "X-Forwarded-Proto": "https"})
    assert fake.redirect_args[0] == "https://trojan-odyssey.northcentralus.cloudapp.azure.com/api/v1/auth/callback"


def test_callback_url_follows_frontend_proxy_host(setup):
    """Frontend host proxying /api (e.g. Vercel rewrite) forwards its own host: the callback stays first-party."""
    client, fake = setup
    client.get("/api/v1/auth/login", headers={
        "Host": "trojan-odyssey.northcentralus.cloudapp.azure.com", "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "fof.example.edu"})
    assert fake.redirect_args[0] == "https://fof.example.edu/api/v1/auth/callback"


@pytest.mark.parametrize("weak", [
    "short-secret",
    "# required for sign-in; same value on every API process",   # .env comment read as the value
    "x" * 20 + " " + "y" * 20,
])
def test_weak_secret_key_disables_sign_in(weak):
    app, _ = make_app(SECRET_KEY=weak)
    url, q = query(app.test_client().get("/api/v1/auth/login", query_string={"next": f"{FE}/"}))
    assert url == f"{FE}/login" and q["error"] == "unavailable"


def test_weak_secret_key_disables_dev_login():
    app, _ = make_app(SECRET_KEY="short", AUTH_DEV_LOGIN=True)
    _, q = query(app.test_client().get("/api/v1/auth/dev/login", query_string={"email": "a@usc.edu"}))
    assert q["error"] == "unavailable"


def test_env_example_has_no_inline_comments():
    """Compose turns `KEY=   # comment` into the value '# comment' (this once set a public SECRET_KEY)."""
    path = Path(__file__).resolve().parents[2] / ".env.example"
    if not path.exists():
        pytest.skip(".env.example not available (backend-only checkout)")
    bad = [line for line in path.read_text().splitlines()
           if not line.lstrip().startswith("#") and "=" in line and "#" in line.split("=", 1)[1]]
    assert not bad, bad
