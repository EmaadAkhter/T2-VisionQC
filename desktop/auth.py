"""Supabase authentication and organization membership for the desktop app.

Session tokens are cached locally so the app keeps working offline after a
successful online sign-in; high-privilege cloud actions still require the
session to be valid. No service-role key is ever used here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

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
        self._restore_session()

    # -- session persistence ------------------------------------------------

    def _restore_session(self) -> bool:
        session_path = paths.session_path()
        if not session_path.exists():
            return False
        try:
            tokens = json.loads(session_path.read_text())
            self.client.auth.set_session(
                tokens["access_token"], tokens["refresh_token"]
            )
            user = self.client.auth.get_user()
            if user and user.user:
                self.user_email = user.user.email
            return True
        except Exception:  # noqa: BLE001 - stale/invalid session
            self._clear_session()
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
        }))

    def _clear_session(self) -> None:
        self.user_email = None
        self.orgs = []
        paths.session_path().unlink(missing_ok=True)

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
        self._save_session()
        self.refresh_orgs()

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
            self._save_session()
            self.refresh_orgs()

    def sign_out(self) -> None:
        try:
            self.client.auth.sign_out()
        except Exception:  # noqa: BLE001
            pass
        self._clear_session()

    # -- organizations ------------------------------------------------------

    def refresh_orgs(self) -> list[OrgContext]:
        try:
            rows = self.client.table("my_orgs").select("*").execute().data
        except Exception as exc:  # noqa: BLE001
            raise AuthError(f"Could not load organizations: {exc}") from exc
        self.orgs = [
            OrgContext(org_id=row["id"], org_name=row["name"], role=row["role"])
            for row in rows
        ]
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
