"""Dashboard auth helpers — basic auth via env credentials."""

from __future__ import annotations

import hmac
import os


def dashboard_credentials_configured() -> bool:
    return bool(os.environ.get("DASHBOARD_USER")) and bool(os.environ.get("DASHBOARD_PASSWORD"))


def verify_dashboard_login(username: str, password: str) -> bool:
    expected_user = os.environ.get("DASHBOARD_USER") or ""
    expected_password = os.environ.get("DASHBOARD_PASSWORD") or ""
    if not expected_user or not expected_password:
        return False
    user_ok = hmac.compare_digest(username, expected_user)
    pass_ok = hmac.compare_digest(password, expected_password)
    return user_ok and pass_ok
