"""Cognito-compatible JWT validation with injected, cached JWKS retrieval."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.request import urlopen

import jwt


class AuthenticationError(ValueError):
    """Raised for missing or invalid bearer credentials."""


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    username: str | None
    token_use: str
    client_id: str | None


class JWKSProvider:
    """Retrieve and cache Cognito public keys by key ID."""

    def __init__(self, url: str, fetcher: Callable[[str], dict] | None = None,
                 cache_ttl_seconds: int = 3600) -> None:
        if not url.startswith(("https://", "http://")):
            raise ValueError("JWKS URL must use HTTP or HTTPS")
        self.url = url
        self.fetcher = fetcher or self._fetch
        self.cache_ttl_seconds = cache_ttl_seconds
        self._keys: dict[str, tuple[float, Any]] = {}

    def get_key(self, kid: str) -> Any:
        now = time.monotonic()
        cached = self._keys.get(kid)
        if cached and cached[0] > now:
            return cached[1]
        try:
            document = self.fetcher(self.url)
            keys = document.get("keys", [])
            for jwk in keys:
                key_id = jwk.get("kid")
                if key_id:
                    self._keys[key_id] = (
                        now + self.cache_ttl_seconds,
                        jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk)),
                    )
        except Exception as exc:
            raise AuthenticationError("authentication key retrieval failed") from exc
        key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError("authentication key unavailable")
        return key[1]

    @staticmethod
    def _fetch(url: str) -> dict:
        with urlopen(url, timeout=5) as response:  # nosec B310 - URL is configured
            return json.loads(response.read())


class CognitoJWTValidator:
    """Validate signed Cognito access or ID tokens and expose minimal identity."""

    def __init__(self, issuer: str, client_id: str, jwks: JWKSProvider,
                 token_use: str = "access") -> None:
        if token_use not in {"access", "id"}:
            raise ValueError("token_use must be 'access' or 'id'")
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.jwks = jwks
        self.token_use = token_use

    def validate(self, token: str) -> AuthenticatedUser:
        if not token or token.count(".") != 2:
            raise AuthenticationError("invalid authentication credentials")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise AuthenticationError("invalid authentication credentials")
            key = self.jwks.get_key(header["kid"])
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self.issuer,
                options={"verify_aud": self.token_use == "id"},
                audience=self.client_id if self.token_use == "id" else None,
            )
            if claims.get("token_use") != self.token_use:
                raise AuthenticationError("invalid authentication credentials")
            if self.token_use == "access" and claims.get("client_id") != self.client_id:
                raise AuthenticationError("invalid authentication credentials")
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip():
                raise AuthenticationError("invalid authentication credentials")
            return AuthenticatedUser(subject, claims.get("username"), self.token_use, claims.get("client_id"))
        except AuthenticationError:
            raise
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid authentication credentials") from exc


def authorization_token(header: str | None) -> str:
    if not header or not header.startswith("Bearer ") or not header[7:].strip():
        raise AuthenticationError("authentication required")
    return header[7:].strip()