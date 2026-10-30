"""Who is this buyer? Checks the Supabase access token the website sends and reads the person out of it.

Supabase projects sign tokens one of two ways: with a private key whose public half is published at the project's JWKS address
(new projects), or with one shared HS256 secret (older ones). Both are accepted; the token's own header says which one it used, but
the algorithm is only trusted if it is on the list for that kind of key, so a token cannot pick its way around the check.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt
from fastapi import Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

ASYMMETRIC = ("ES256", "RS256", "EdDSA")
AUDIENCE = "authenticated"


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class BuyerIdentity:
    user_id: str
    email: str | None
    full_name: str | None

    @property
    def thread_id(self) -> str:
        return f"web:{self.user_id}"


class TokenVerifier:
    def __init__(self, *, supabase_url: str = "", jwt_secret: str = "", jwks_client: Any | None = None):
        self._issuer = f"{supabase_url.rstrip('/')}/auth/v1" if supabase_url else None
        self._secret = jwt_secret
        if jwks_client is None and supabase_url:
            jwks_client = jwt.PyJWKClient(f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=5)
        self._jwks = jwks_client

    def verify(self, token: str) -> BuyerIdentity:
        """Blocking when the public keys have to be fetched, so callers on the event loop use `verify_async`."""
        try:
            alg = jwt.get_unverified_header(token).get("alg")
            if alg == "HS256":
                if not self._secret:
                    raise TokenError("This server does not accept shared-secret tokens")
                key: Any = self._secret
                algorithms = ["HS256"]
            elif alg in ASYMMETRIC:
                if self._jwks is None:
                    raise TokenError("SUPABASE_URL is not configured")
                key = self._jwks.get_signing_key_from_jwt(token).key
                algorithms = [alg]
            else:
                raise TokenError("Unsupported token type")
            claims = jwt.decode(
                token, key, algorithms=algorithms, audience=AUDIENCE, issuer=self._issuer,
                options={"require": ["exp", "sub"], "verify_iss": self._issuer is not None},
            )
        except TokenError:
            raise
        except jwt.PyJWTError as exc:
            raise TokenError("That sign-in is not valid or has expired") from exc
        return _identity(claims)

    async def verify_async(self, token: str) -> BuyerIdentity:
        return await run_in_threadpool(self.verify, token)


def _identity(claims: dict[str, Any]) -> BuyerIdentity:
    if claims.get("is_anonymous"):
        raise TokenError("Please sign in with an email address")
    try:
        user_id = str(UUID(str(claims["sub"])))
    except ValueError as exc:
        raise TokenError("That sign-in is not valid") from exc
    meta = claims.get("user_metadata") if isinstance(claims.get("user_metadata"), dict) else {}
    name = str(meta.get("full_name") or meta.get("name") or "").strip()[:120] or None
    email = str(claims.get("email") or "").strip().lower()[:254] or None
    return BuyerIdentity(user_id=user_id, email=email, full_name=name)


def verifier_of(request: Request) -> TokenVerifier:
    state = request.app.state
    verifier = getattr(state, "token_verifier", None)
    if verifier is None:
        s = state.settings
        verifier = state.token_verifier = TokenVerifier(supabase_url=s.supabase_url, jwt_secret=s.supabase_jwt_secret)
    return verifier


async def current_buyer(request: Request, authorization: str | None = Header(default=None)) -> BuyerIdentity:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Please sign in")
    try:
        return await verifier_of(request).verify_async(token.strip())
    except TokenError as exc:
        raise HTTPException(401, str(exc)) from None
