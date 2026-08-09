"""Cloudflare Access JWT validation for dashboard auto-login.

When the dashboard sits behind a Cloudflare Access application (e.g. a
Cloudflare Tunnel with a Zero Trust policy), every request that passed the
Access login carries a ``Cf-Access-Jwt-Assertion`` header: an RS256-signed JWT
issued by the operator's team domain. Verifying that signature against the
team's published JWKS proves the request went through Access — a client that
reaches the gateway directly (LAN, port-forward) cannot forge it — so the
token-auth middleware can mint a dashboard session without the token-URL
paste step.

Verification is RSASSA-PKCS1-v1_5 over SHA-256, implemented directly on the
JWKS ``n``/``e`` integers (RFC 8017 §8.2.2). Only signature *verification* is
performed — public-key math on public inputs — so no cryptography dependency
is needed.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field

import aiohttp

logger = logging.getLogger(__name__)

# Header Cloudflare attaches to every request that passed an Access policy.
CF_ACCESS_HEADER = "Cf-Access-Jwt-Assertion"

# JWKS endpoint path on the team domain (Cloudflare-defined, stable).
_CERTS_PATH = "/cdn-cgi/access/certs"
# Serve JWKS from cache this long before refetching (Cloudflare rotates keys
# rarely; unknown-kid handling below covers a rotation inside the window).
_JWKS_CACHE_TTL_SECS = 3600
# Minimum spacing between fetches triggered by an unknown ``kid``, so a flood
# of garbage assertions cannot hammer the certs endpoint.
_JWKS_REFETCH_COOLDOWN_SECS = 60
_FETCH_TIMEOUT_SECS = 10
# Clock-skew tolerance applied to ``exp`` and ``nbf``.
_CLOCK_LEEWAY_SECS = 60

# ASN.1 DigestInfo prefix for SHA-256 (RFC 8017 §9.2 note 1).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def normalize_team_domain(team_domain: str) -> str:
    """Reduce a configured team domain to a bare hostname.

    Accepts ``myteam.cloudflareaccess.com``, ``https://myteam.cloudflareaccess.com``
    or the same with a trailing slash, so config typos in scheme/slash don't
    silently disable the feature.
    """
    host = team_domain.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    return host.strip("/").lower()


def _rs256_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 SHA-256 verification (RFC 8017 §8.2.2)."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    m = pow(int.from_bytes(signature, "big"), e, n)
    em = m.to_bytes(k, "big")
    ps_len = k - 3 - len(_SHA256_DIGEST_INFO) - hashlib.sha256().digest_size
    if ps_len < 8:
        return False
    expected = (
        b"\x00\x01"
        + b"\xff" * ps_len
        + b"\x00"
        + _SHA256_DIGEST_INFO
        + hashlib.sha256(message).digest()
    )
    # Not secret material (public-key verify), but constant-time compare is free.
    return hmac.compare_digest(em, expected)


@dataclass
class _JwksEntry:
    keys: dict[str, tuple[int, int]] = field(default_factory=dict)
    fetched_at: float = 0.0
    last_attempt: float = 0.0


_jwks_cache: dict[str, _JwksEntry] = {}
_jwks_lock = asyncio.Lock()


def clear_jwks_cache() -> None:
    """Test hook: drop all cached JWKS entries."""
    _jwks_cache.clear()


async def _fetch_jwks(host: str) -> dict[str, tuple[int, int]]:
    """Fetch the team's JWKS and return ``{kid: (n, e)}`` for its RSA keys."""
    url = f"https://{host}{_CERTS_PATH}"
    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
    keys: dict[str, tuple[int, int]] = {}
    for jwk in data.get("keys", []):
        if jwk.get("kty") != "RSA":
            continue
        kid = str(jwk.get("kid", ""))
        try:
            n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
            e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
        except (KeyError, ValueError, binascii.Error):
            continue
        if kid and n and e:
            keys[kid] = (n, e)
    return keys


async def _get_keys(host: str, kid: str) -> dict[str, tuple[int, int]]:
    """Return cached JWKS for *host*, refetching when stale or *kid* is unknown.

    A fetch failure degrades to whatever is cached (possibly empty) so a
    transient certs-endpoint outage rejects only assertions signed by keys we
    have never seen, instead of raising into the auth middleware.
    """
    async with _jwks_lock:
        entry = _jwks_cache.setdefault(host, _JwksEntry())
        now = time.time()
        fresh = entry.keys and now - entry.fetched_at < _JWKS_CACHE_TTL_SECS
        if fresh and kid in entry.keys:
            return entry.keys
        # Stale, empty, or unknown kid (possible key rotation): refetch, but
        # never more often than the cooldown so unknown-kid garbage can't turn
        # this into an outbound request amplifier.
        if now - entry.last_attempt < _JWKS_REFETCH_COOLDOWN_SECS:
            return entry.keys
        entry.last_attempt = now
        try:
            entry.keys = await _fetch_jwks(host)
            entry.fetched_at = now
        except Exception as exc:
            logger.warning("cf_access: JWKS fetch from %s failed: %s", host, exc)
        return entry.keys


async def validate_cf_access_jwt(
    assertion: str, team_domain: str, aud: str
) -> tuple[bool, str, str]:
    """Validate a ``Cf-Access-Jwt-Assertion`` value.

    Returns ``(valid, email, reason)``. Checks — in order — structure, RS256
    algorithm pin, signature against the team JWKS, issuer, audience, and the
    ``exp``/``nbf`` window, then requires a non-empty ``email`` claim (identity
    logins; Access service tokens carry no email and are deliberately not
    accepted as dashboard users).
    """
    host = normalize_team_domain(team_domain)
    if not host or not aud:
        return False, "", "cf_access not configured"
    parts = assertion.split(".")
    if len(parts) != 3:
        return False, "", "malformed assertion"
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except (ValueError, binascii.Error):
        return False, "", "undecodable assertion"
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return False, "", "undecodable assertion"
    # Algorithm is pinned server-side: the header's alg is attacker-controlled
    # and must never select the verification method (alg-confusion attacks).
    if header.get("alg") != "RS256":
        return False, "", "alg not RS256"
    kid = str(header.get("kid", ""))
    if not kid:
        return False, "", "no kid"
    keys = await _get_keys(host, kid)
    if kid not in keys:
        return False, "", "unknown kid"
    n, e = keys[kid]
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii", errors="replace")
    if not _rs256_verify(n, e, signing_input, signature):
        return False, "", "bad signature"
    if payload.get("iss") != f"https://{host}":
        return False, "", "issuer mismatch"
    aud_claim = payload.get("aud")
    aud_list = aud_claim if isinstance(aud_claim, list) else [aud_claim]
    if aud not in aud_list:
        return False, "", "audience mismatch"
    now = time.time()
    try:
        exp = float(payload.get("exp", 0))
        nbf = float(payload.get("nbf", 0))
    except (TypeError, ValueError):
        return False, "", "bad time claims"
    if exp <= now - _CLOCK_LEEWAY_SECS:
        return False, "", "assertion expired"
    if nbf > now + _CLOCK_LEEWAY_SECS:
        return False, "", "assertion not yet valid"
    email = payload.get("email")
    if not isinstance(email, str) or not email:
        return False, "", "no email claim"
    return True, email, ""
