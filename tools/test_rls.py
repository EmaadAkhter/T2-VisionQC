"""Verify auth, roles and tenant isolation against local Supabase.

Usage:
    python3 tools/test_rls.py

Uses the public anon key for user clients and the service-role key only to
provision throwaway test users. Exits non-zero if any check fails.
"""

import json
import subprocess
import sys
from pathlib import Path

from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "desktop" / "config.local.json").read_text())
URL = CONFIG["supabase_url"]
ANON = CONFIG["supabase_anon_key"]
DEMO_ORG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok)))
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {name}" + (f"  ({detail})" if detail else ""))


def anon_client():
    return create_client(URL, ANON)


def service_key() -> str:
    result = subprocess.run(
        ["supabase", "status", "-o", "json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    return json.loads(result.stdout)["SERVICE_ROLE_KEY"]


def ensure_user(admin, email: str, password: str) -> None:
    try:
        admin.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
        })
    except Exception as exc:  # noqa: BLE001 - already-exists is fine
        if "already" not in str(exc).lower() and "exists" not in str(exc).lower():
            raise


def main() -> int:
    service = create_client(URL, service_key())
    admin = create_client(URL, service_key())

    # Idempotency: remove leftovers from any previous interrupted run.
    service.table("inspections").delete().like("uid", "RLS-TEST-%").execute()
    service.table("orgs").delete().neq("id", DEMO_ORG).execute()

    # --- seeded users -----------------------------------------------------
    owner = anon_client()
    owner.auth.sign_in_with_password(
        {"email": "owner@visionqc.local", "password": "visionqc123"}
    )
    check("owner can sign in", owner.auth.get_user() is not None)

    orgs = owner.table("my_orgs").select("*").execute().data
    check("owner sees demo org as owner",
          len(orgs) == 1 and orgs[0]["role"] == "owner", str(orgs))

    operator = anon_client()
    operator.auth.sign_in_with_password(
        {"email": "operator@visionqc.local", "password": "visionqc123"}
    )
    analyst = anon_client()
    analyst.auth.sign_in_with_password(
        {"email": "analyst@visionqc.local", "password": "visionqc123"}
    )

    # --- operator can log an inspection ----------------------------------
    inserted = operator.table("inspections").insert({
        "org_id": DEMO_ORG,
        "uid": "RLS-TEST-OP-1",
        "verdict": "PASS",
        "score": 0.2,
    }).execute()
    check("operator can insert inspection", len(inserted.data) == 1)

    # --- analyst is read-only --------------------------------------------
    try:
        analyst.table("inspections").insert({
            "org_id": DEMO_ORG,
            "uid": "RLS-TEST-AN-1",
            "verdict": "PASS",
        }).execute()
        check("analyst cannot insert inspection", False, "insert succeeded")
    except Exception as exc:  # noqa: BLE001
        check("analyst cannot insert inspection", True, type(exc).__name__)

    try:
        analyst.table("memberships").insert({
            "org_id": DEMO_ORG,
            "user_id": "44444444-4444-4444-8444-444444444444",
            "role": "owner",
        }).execute()
        check("analyst cannot modify memberships", False, "insert succeeded")
    except Exception as exc:  # noqa: BLE001
        check("analyst cannot modify memberships", True, type(exc).__name__)

    # --- admin can invite ------------------------------------------------
    admin_user = anon_client()
    admin_user.auth.sign_in_with_password(
        {"email": "admin@visionqc.local", "password": "visionqc123"}
    )
    invite = admin_user.table("invitations").insert({
        "org_id": DEMO_ORG,
        "email": "newhire@visionqc.local",
        "role": "operator",
        "invited_by": "22222222-2222-4222-8222-222222222222",
    }).execute()
    check("admin can create invitation", len(invite.data) == 1)
    invite_token = invite.data[0]["token"] if invite.data else None

    # --- outsider: no visibility, no writes ------------------------------
    ensure_user(admin, "outsider@visionqc.local", "outsider123")
    outsider = anon_client()
    outsider.auth.sign_in_with_password(
        {"email": "outsider@visionqc.local", "password": "outsider123"}
    )
    check("outsider sees no orgs",
          len(outsider.table("my_orgs").select("*").execute().data) == 0)
    check("outsider reads no inspections",
          len(outsider.table("inspections").select("*").execute().data) == 0)
    check("outsider reads no cameras",
          len(outsider.table("cameras").select("*").execute().data) == 0)

    try:
        outsider.table("inspections").insert({
            "org_id": DEMO_ORG,
            "uid": "RLS-TEST-OUT-1",
        }).execute()
        check("outsider cannot insert into demo org", False, "insert succeeded")
    except Exception as exc:  # noqa: BLE001
        check("outsider cannot insert into demo org", True, type(exc).__name__)

    # --- outsider can create their own org via RPC ------------------------
    created = outsider.rpc("create_org", {"org_name": "Outsider Works"}).execute()
    check("outsider can create own org", bool(created.data))
    outsider_orgs = outsider.table("my_orgs").select("*").execute().data
    check("outsider sees only own org",
          len(outsider_orgs) == 1 and outsider_orgs[0]["role"] == "owner",
          str(outsider_orgs))

    # --- invitation acceptance -------------------------------------------
    if invite_token:
        ensure_user(admin, "newhire@visionqc.local", "newhire123")
        newhire = anon_client()
        newhire.auth.sign_in_with_password(
            {"email": "newhire@visionqc.local", "password": "newhire123"}
        )
        accepted = newhire.rpc(
            "accept_invitation", {"invite_token": invite_token}
        ).execute()
        check("invited user can accept invitation", bool(accepted.data))
        roles = newhire.table("my_orgs").select("*").execute().data
        check("invited user has operator role",
              any(r["id"] == DEMO_ORG and r["role"] == "operator" for r in roles),
              str(roles))
        try:
            newhire.rpc("accept_invitation",
                        {"invite_token": invite_token}).execute()
            check("invitation cannot be reused", False, "second accept succeeded")
        except Exception as exc:  # noqa: BLE001
            check("invitation cannot be reused", True, type(exc).__name__)

    # --- cleanup ----------------------------------------------------------
    service.table("inspections").delete().eq("uid", "RLS-TEST-OP-1").execute()
    service.table("invitations").delete().eq("org_id", DEMO_ORG).execute()

    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("Failed:")
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
