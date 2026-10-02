"""Supabase authentication and organization membership for the desktop app.

Session tokens, the user's organization list and the last active organization
are cached locally so the app restores straight into the main window on
relaunch — online or offline. High-privilege cloud actions still require the
session to be valid. No service-role key is ever used here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from supabase import Client, create_client

import paths
from desktop.config import load_config


@dataclass
class OrgContext:
    org_id: str
    org_name: str
    role: str


class AuthError(Exception):
    pass


class AuthService:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        if not self.config.get("supabase_url") or not self.config.get("supabase_anon_key"):
            raise AuthError(
                "Supabase is not configured. Run `supabase start` and "
                "`python3 tools/write_supabase_env.py` first."
            )
        self.client: Client = create_client(
            self.config["supabase_url"], self.config["supabase_anon_key"]
        )
        self.user_email: str | None = None
        self.orgs: list[OrgContext] = []
        self.last_org_id: str | None = None
        self.session_state: str = "none"  # none | valid | offline | invalid
        self._restore_session()

    # -- session persistence ------------------------------------------------

    def _restore_session(self) -> bool:
        """Restore the cached session.

        Sets `session_state`:
        - valid:   tokens accepted by the server
        - offline: tokens present but the server is unreachable (use cache)
        - invalid: server rejected the session (user must sign in again)
        """
        session_path = paths.session_path()
        if not session_path.exists():
            self.session_state = "none"
            return False
        try:
            data = json.loads(session_path.read_text())
            self.client.auth.set_session(
                data["access_token"], data["refresh_token"]
            )
            self.user_email = data.get("email")
            self.orgs = [
                OrgContext(**org) for org in data.get("orgs", [])
                if isinstance(org, dict) and {"org_id", "org_name", "role"} <= set(org)
            ]
            self.last_org_id = data.get("last_org_id")
            try:
                user = self.client.auth.get_user()
                self.user_email = (
                    user.user.email if user and user.user else self.user_email
                )
                self.session_state = "valid" if self.user_email else "invalid"
            except Exception as exc:  # noqa: BLE001
                if _looks_like_auth_failure(exc):
                    self._clear_session()
                    self.session_state = "invalid"
                    return False
                # Network problem: keep the cached session and orgs.
                self.session_state = "offline"
            return bool(self.user_email)
        except Exception:  # noqa: BLE001 - corrupt session file
            self._clear_session()
            self.session_state = "invalid"
            return False

    def _save_session(self) -> None:
        session = self.client.auth.get_session()
        if session is None:
            return
        session_path = paths.session_path()
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(json.dumps({
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "email": self.user_email,
            "orgs": [asdict(org) for org in self.orgs],
            "last_org_id": self.last_org_id,
        }))

    def _clear_session(self) -> None:
        self.user_email = None
        self.orgs = []
        self.last_org_id = None
        paths.session_path().unlink(missing_ok=True)

    def remember_org(self, org_id: str) -> None:
        """Persist the active organization for the next launch."""
        self.last_org_id = org_id
        self._save_session()

    # -- auth actions -------------------------------------------------------

    def sign_in(self, email: str, password: str) -> None:
        try:
            result = self.client.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"Sign-in failed: {exc}") from exc
        if result.user is None:
            raise AuthError("Sign-in failed: no user returned")
        self.user_email = result.user.email
        self.session_state = "valid"
        self.refresh_orgs()
        self._save_session()

    def sign_up(self, email: str, password: str, full_name: str) -> None:
        try:
            result = self.client.auth.sign_up({
                "email": email,
                "password": password,
                "options": {"data": {"full_name": full_name}},
            })
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"Sign-up failed: {exc}") from exc
        if result.session is not None:
            self.user_email = email
            self.session_state = "valid"
            self.refresh_orgs()
            self._save_session()

    def sign_out(self) -> None:
        try:
            self.client.auth.sign_out()
        except Exception:  # noqa: BLE001
            pass
        self._clear_session()

    # -- organizations ------------------------------------------------------

    def claim_invitations(self) -> list[str]:
        """Accept any pending invitation addressed to this account.

        Admins create invitations in the web console; the desktop simply
        claims them after sign-in. Returns the affected organization ids.
        """
        try:
            result = self.client.rpc("claim_invitations").execute()
        except Exception:  # noqa: BLE001 - older servers may not have the RPC
            return []
        return list(result.data or [])

    def refresh_orgs(self) -> list[OrgContext]:
        try:
            rows = self.client.table("my_orgs").select("*").execute().data
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"Could not load organizations: {exc}") from exc
        self.orgs = [
            OrgContext(org_id=row["id"], org_name=row["name"], role=row["role"])
            for row in rows
        ]
        if self.last_org_id not in {org.org_id for org in self.orgs}:
            self.last_org_id = self.orgs[0].org_id if self.orgs else None
        self._save_session()
        return self.orgs

    def create_org(self, name: str) -> OrgContext:
        try:
            result = self.client.rpc("create_org", {"org_name": name}).execute()
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"Could not create organization: {exc}") from exc
        if not result.data:
            raise AuthError("Organization creation returned no id")
        self.refresh_orgs()
        for org in self.orgs:
            if org.org_id == result.data:
                return org
        return OrgContext(org_id=result.data, org_name=name, role="owner")

    def is_online(self) -> bool:
        try:
            self.client.table("my_orgs").select("id").limit(1).execute()
            return True
        except Exception:  # noqa: BLE001
            return False


def _looks_like_auth_failure(exc: Exception) -> bool:
    """True when the server rejected the session (vs a network problem)."""
    text = f"{type(exc).__name__} {exc}".lower()
    markers = (
        "401", "403", "invalid", "expired", "jwt", "token",
        "refresh", "not authenticated", "session",
    )
    return any(marker in text for marker in markers)
