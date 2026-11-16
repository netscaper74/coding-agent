"""Demo identity: maps bearer tokens to users, teams and access scopes.

In production, replace `resolve_token` with a lookup against GitLab (the developer's
personal access token -> their groups / projects) or your SSO provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from .settings import get_settings

PUBLIC_SCOPE = "public"


@dataclass(frozen=True)
class User:
    user: str
    name: str
    team: str
    role: str = "developer"
    groups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def scopes(self) -> list[str]:
        return sorted(set(self.groups) | {PUBLIC_SCOPE})

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@lru_cache(maxsize=1)
def _directory() -> tuple[dict[str, User], dict[str, User]]:
    by_token: dict[str, User] = {}
    by_name: dict[str, User] = {}
    for entry in get_settings().yaml("users.yaml").get("users", []):
        u = User(
            user=entry["user"],
            name=entry.get("name", entry["user"]),
            team=entry.get("team", "unknown"),
            role=entry.get("role", "developer"),
            groups=tuple(entry.get("groups", [])),
        )
        by_token[str(entry["token"])] = u
        by_name[u.user] = u
    return by_token, by_name


def resolve_token(token: str | None) -> User | None:
    if not token:
        return None
    return _directory()[0].get(token.strip())


def get_user(name: str) -> User | None:
    return _directory()[1].get(name)


def all_users() -> list[User]:
    return list(_directory()[1].values())


def bearer(headers) -> str | None:
    value = headers.get("authorization") or headers.get("Authorization") or ""
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return headers.get("x-api-key") or None
