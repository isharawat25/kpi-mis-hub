"""Lightweight identity: X-User / X-Role headers.

Replace with Azure AD / OAuth2 JWT validation in production; route code only depends on CurrentUser.
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

ROLES = {"submitter", "approver", "admin"}


@dataclass
class CurrentUser:
    name: str
    role: str


def current_user(x_user: str | None = Header(default=None), x_role: str | None = Header(default=None)) -> CurrentUser:
    if not x_user or not x_user.strip():
        raise HTTPException(401, "Missing X-User header")
    role = (x_role or "submitter").lower()
    if role not in ROLES:
        raise HTTPException(400, f"Invalid role '{role}'")
    return CurrentUser(x_user.strip(), role)


def require(*roles: str):
    def checker(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.role not in roles and user.role != "admin":
            raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return user
    return checker
