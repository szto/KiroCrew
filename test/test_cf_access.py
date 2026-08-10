"""Tests for Cloudflare Access JWT validation and middleware auto-login."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web

import kiro_crew.dashboard.cf_access as cf_access
from kiro_crew.dashboard.cf_access import (
    CF_ACCESS_HEADER,
    _rs256_verify,
    normalize_team_domain,
    validate_cf_access_jwt,
)
from kiro_crew.dashboard.token_auth import token_auth_middleware, validate_token

# Static 2048-bit RSA keypair for tests only (never used outside this file).
# Embedded so the suite needs no cryptography dependency: signing below is
# plain RSASSA-PKCS1-v1_5 integer math with the private exponent.
N = int(
    "ce22633daf9fef9de4d06b54ef02121362ca740f56f70489ea4c61ca7b76db81"
    "4c698a5faeeb6586744632e2aab3484f3ff737f4fa6f26d5340bad2fffa307a7"
    "3ee83e41a6b692993f4841d0ce335bbf63aa414ccfd99181d79c70bebef9506c"
    "a068f1f1a18464751ca41b170149a464898483b2183c2f1bb03cfaf86c61a1fa"
    "d7eb032957e73855d86337b4848f2478a16e46a6e37aab23b590853f9ba0b386"
    "0a98709730c6d969e1ebd681704c7962bdf57d3898aff89d04c84ae667b5aec7"
    "1954ce7c0a6a9daa0480966c65cacd53076e79ea8b5c4a76eb77ac56e8abdde6"
    "d289e209993f24175623cf2e4d7b60c9237aac7b514171461b1b10d485ff0997",
    16,
)
E = 65537
D = int(
    "8aa0eb1d112a53d106e679487aca145df53d327b1e81570c14064a68b79fb7aa"
    "67e3e800c6cd334f63e327559e774954ad856a9c81253f78785d61f1107b13d5"
    "3ba3f4e5320e96de23db9f124e453beea5985aa77876aee4ce46e78c0b38a05d"
    "c2d8d1332f9759f813759f6d03f0ad739a5daede97188d4091c5c6584be0b28d"
    "9f33703953b5ea454e8211a082b44504f9d7d8249e3ece347cf00b3256a29a53"
    "8fcbcc3a06e3f9a4b33c1662394b809ce54c79949cc0ab7eea2ca1cf83ceb87c"
    "d2742e7e3c3c19a19a4dd484521cf665129d000e2f3409cb058b5461ed768910"
    "1be3ac898df56a407d6e40b7bc52fea874103435b110bbb7807c3e26b28ed9",
    16,
)
KID = "test-kid-1"
TEAM = "myteam.cloudflareaccess.com"
AUD = "a0b1c2d3e4f5-test-aud"

_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

# Sentinel: make_assertion drops a default claim entirely when its override
# value is this object (as opposed to overriding it with a falsy value).
DROP = object()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_rs256(signing_input: bytes) -> bytes:
    digest = hashlib.sha256(signing_input).digest()
    k = (N.bit_length() + 7) // 8
    ps = b"\xff" * (k - 3 - len(_SHA256_PREFIX) - len(digest))
    em = b"\x00\x01" + ps + b"\x00" + _SHA256_PREFIX + digest
    return pow(int.from_bytes(em, "big"), D, N).to_bytes(k, "big")


def make_assertion(
    payload_overrides: dict | None = None,
    header_overrides: dict | None = None,
    *,
    break_signature: bool = False,
) -> str:
    now = time.time()
    payload: dict = {
        "iss": f"https://{TEAM}",
        "aud": [AUD],
        "email": "user@example.com",
        "exp": now + 300,
        "nbf": now - 60,
        "iat": now,
    }
    for key, value in (payload_overrides or {}).items():
        if value is DROP:
            payload.pop(key, None)
        else:
            payload[key] = value
    header: dict = {"alg": "RS256", "kid": KID, "typ": "JWT"}
    for key, value in (header_overrides or {}).items():
        if value is DROP:
            header.pop(key, None)
        else:
            header[key] = value
    signing_input = (
        _b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(payload).encode())
    )
    signature = _sign_rs256(signing_input.encode("ascii"))
    if break_signature:
        signature = bytes([signature[0] ^ 0x01]) + signature[1:]
    return signing_input + "." + _b64url(signature)


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch):
    """Serve the test key as the team JWKS; count fetches; isolate the cache."""
    cf_access.clear_jwks_cache()
    calls = {"n": 0}

    async def fake_fetch(host: str) -> dict[str, tuple[int, int]]:
        calls["n"] += 1
        return {KID: (N, E)}

    monkeypatch.setattr(cf_access, "_fetch_jwks", fake_fetch)
    yield calls
    cf_access.clear_jwks_cache()


@pytest.fixture(autouse=True)
def isolate_token_state(tmp_path, monkeypatch):
    """Keep minted-session side effects (revocation files) out of the real home."""
    import kiro_crew.dashboard.token_auth as _ta

    monkeypatch.setattr("kiro_crew.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(_ta, "_revoked_store_singleton", None)
    _ta._state.clear_all()
    yield
    monkeypatch.setattr(_ta, "_revoked_store_singleton", None)
    _ta._state.clear_all()


# -- normalize_team_domain --


@pytest.mark.parametrize(
    "raw",
    [
        "myteam.cloudflareaccess.com",
        "https://myteam.cloudflareaccess.com",
        "https://myteam.cloudflareaccess.com/",
        "  MyTeam.CloudflareAccess.com  ",
    ],
)
def test_normalize_team_domain(raw: str) -> None:
    assert normalize_team_domain(raw) == "myteam.cloudflareaccess.com"


# -- _rs256_verify primitives --


def test_rs256_verify_roundtrip() -> None:
    message = b"hello world"
    assert _rs256_verify(N, E, message, _sign_rs256(message)) is True


def test_rs256_verify_rejects_wrong_length_signature() -> None:
    assert _rs256_verify(N, E, b"msg", b"\x00" * 12) is False


# -- validate_cf_access_jwt --


@pytest.mark.asyncio
async def test_valid_assertion_accepted() -> None:
    valid, email, reason = await validate_cf_access_jwt(make_assertion(), TEAM, AUD)
    assert (valid, email, reason) == (True, "user@example.com", "")


@pytest.mark.asyncio
async def test_string_aud_claim_accepted() -> None:
    assertion = make_assertion({"aud": AUD})
    valid, email, _ = await validate_cf_access_jwt(assertion, TEAM, AUD)
    assert valid is True and email == "user@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_overrides, expected_reason",
    [
        ({"exp": time.time() - 600}, "assertion expired"),
        ({"nbf": time.time() + 600}, "assertion not yet valid"),
        ({"iss": "https://evil.example.com"}, "issuer mismatch"),
        ({"aud": ["other-app-aud"]}, "audience mismatch"),
        ({"email": DROP}, "no email claim"),
        ({"email": ""}, "no email claim"),
        ({"exp": "soon"}, "bad time claims"),
    ],
)
async def test_bad_claims_rejected(payload_overrides: dict, expected_reason: str) -> None:
    assertion = make_assertion(payload_overrides)
    valid, email, reason = await validate_cf_access_jwt(assertion, TEAM, AUD)
    assert valid is False and email == "" and reason == expected_reason


@pytest.mark.asyncio
async def test_tampered_signature_rejected() -> None:
    assertion = make_assertion(break_signature=True)
    valid, _, reason = await validate_cf_access_jwt(assertion, TEAM, AUD)
    assert valid is False and reason == "bad signature"


@pytest.mark.asyncio
@pytest.mark.parametrize("alg", ["none", "HS256", "ES256"])
async def test_non_rs256_alg_rejected(alg: str) -> None:
    # Attacker-controlled alg header must never select the verify method.
    assertion = make_assertion(header_overrides={"alg": alg})
    valid, _, reason = await validate_cf_access_jwt(assertion, TEAM, AUD)
    assert valid is False and reason == "alg not RS256"


@pytest.mark.asyncio
async def test_unknown_kid_rejected_without_refetch_inside_cooldown(fake_jwks) -> None:
    # Warm the cache with a good assertion (1 fetch) …
    valid, _, _ = await validate_cf_access_jwt(make_assertion(), TEAM, AUD)
    assert valid is True and fake_jwks["n"] == 1
    # … then an unknown kid inside the cooldown must NOT trigger another
    # outbound fetch (amplification guard) and must be rejected.
    assertion = make_assertion(header_overrides={"kid": "rotated-away"})
    valid, _, reason = await validate_cf_access_jwt(assertion, TEAM, AUD)
    assert valid is False and reason == "unknown kid"
    assert fake_jwks["n"] == 1


@pytest.mark.asyncio
async def test_malformed_assertions_rejected() -> None:
    for junk in ["", "abc", "a.b", "a.b.c.d", "!!.!!.!!"]:
        valid, _, _ = await validate_cf_access_jwt(junk, TEAM, AUD)
        assert valid is False


@pytest.mark.asyncio
async def test_unconfigured_rejects_everything() -> None:
    valid, _, reason = await validate_cf_access_jwt(make_assertion(), "", AUD)
    assert valid is False and reason == "cf_access not configured"
    valid, _, reason = await validate_cf_access_jwt(make_assertion(), TEAM, "")
    assert valid is False and reason == "cf_access not configured"


# -- Middleware integration --


async def _ok_handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def _make_request(
    path: str = "/api/status",
    cookies: dict | None = None,
    remote: str = "10.0.0.1",
    headers: dict | None = None,
    method: str = "GET",
) -> MagicMock:
    req = MagicMock(spec=web.Request)
    req.path = path
    req.query = {}
    req.cookies = cookies or {}
    req.remote = remote
    req.headers = headers or {}
    req.method = method
    return req


def _cf_middleware():
    return token_auth_middleware(
        local_only=False,
        cf_access_team_domain=TEAM,
        cf_access_aud=AUD,
    )


@pytest.mark.asyncio
async def test_middleware_mints_session_from_cf_assertion() -> None:
    mw = _cf_middleware()
    req = _make_request(headers={CF_ACCESS_HEADER: make_assertion()})

    resp = await mw(req, _ok_handler)
    assert resp.status == 200

    access = resp.cookies.get("mc_token_5476")
    assert access is not None
    ok, uid, _ = validate_token(access.value, use_session_exp=True)
    assert ok is True and uid == "user@example.com"
    # Paired refresh cookie minted too, so the SPA's silent-refresh loop works.
    assert resp.cookies.get("mc_refresh_5476") is not None


@pytest.mark.asyncio
async def test_middleware_recovers_expired_cookie_via_cf_assertion() -> None:
    # A stale/garbage access cookie plus a valid assertion re-mints silently.
    mw = _cf_middleware()
    req = _make_request(
        cookies={"mc_token_5476": "not.a.valid.token"},
        headers={CF_ACCESS_HEADER: make_assertion()},
    )

    resp = await mw(req, _ok_handler)
    assert resp.status == 200
    access = resp.cookies.get("mc_token_5476")
    assert access is not None and access.value != "not.a.valid.token"


@pytest.mark.asyncio
async def test_middleware_denies_bad_assertion() -> None:
    mw = _cf_middleware()
    req = _make_request(headers={CF_ACCESS_HEADER: make_assertion(break_signature=True)})
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_middleware_ignores_assertion_when_not_configured() -> None:
    # Without team_domain+aud config the header changes nothing: deny as usual.
    mw = token_auth_middleware(local_only=False)
    req = _make_request(headers={CF_ACCESS_HEADER: make_assertion()})
    resp = await mw(req, _ok_handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_cf_session_is_ip_bound() -> None:
    # The minted session inherits token-URL semantics: IP-pinned on mint.
    mw = _cf_middleware()
    req = _make_request(headers={CF_ACCESS_HEADER: make_assertion()})
    resp = await mw(req, _ok_handler)
    access = resp.cookies.get("mc_token_5476")
    assert access is not None

    from kiro_crew.dashboard.token_auth import check_token_ip

    assert check_token_ip(access.value, "10.0.0.1") is True
    assert check_token_ip(access.value, "203.0.113.9") is False
