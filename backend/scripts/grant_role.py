"""Give a signed-up person a role in an organization. This is how the first owner of an organization is made.

    python -m scripts.grant_role --email broker@example.com --org proppulse --role owner
    python -m scripts.grant_role --user-id <uuid> --org proppulse --role admin

The person must already have an account. The email is looked up in Supabase's own `auth.users` table, which only the database's
owner can read, so this runs from an operator's machine with DATABASE_URL and is never reachable from the website. It replaces the
old BROKER_EMAIL setting: an address anyone could type into a sign-up form no longer decides who runs an agency.
"""
from __future__ import annotations

import argparse
import sys

import psycopg

from app.config import get_settings

ROLES = ("viewer", "agent", "admin", "owner")


def grant(url: str, org_slug: str, role: str, *, email: str | None = None, user_id: str | None = None) -> str:
    """Returns a one-line description of what was done. Raises SystemExit with a reason when it cannot."""
    if role not in ROLES:
        raise SystemExit(f"role must be one of {', '.join(ROLES)}")
    if bool(email) == bool(user_id):
        raise SystemExit("give exactly one of --email and --user-id")
    with psycopg.connect(url) as conn:
        org = conn.execute("select id from organizations where slug = %s", (org_slug,)).fetchone()
        if org is None:
            raise SystemExit(f"no organization with the address '{org_slug}'")
        if email:
            has_auth = conn.execute("select to_regclass('auth.users')").fetchone()[0]
            if not has_auth:
                raise SystemExit("this database has no auth.users table; use --user-id")
            found = conn.execute(
                "select id, email_confirmed_at from auth.users where lower(email) = lower(%s)", (email.strip(),)).fetchone()
            if found is None:
                raise SystemExit(f"nobody has signed up with {email}")
            if found[1] is None:
                raise SystemExit(f"{email} has not confirmed their address yet")
            user_id = str(found[0])
        conn.execute(
            "insert into memberships (org_id, user_id, role) values (%s, %s, %s) "
            "on conflict (org_id, user_id) do update set role = excluded.role", (org[0], user_id, role))
        conn.commit()
    return f"{user_id} is now {role} of {org_slug}"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", required=True, help="the organization's address (slug)")
    ap.add_argument("--role", required=True, choices=ROLES)
    ap.add_argument("--email")
    ap.add_argument("--user-id")
    args = ap.parse_args(argv)
    try:
        print(grant(get_settings().database_url, args.org, args.role, email=args.email, user_id=args.user_id))
    except psycopg.errors.RaiseException as exc:  # demoting the last owner
        sys.exit(str(exc).splitlines()[0])


if __name__ == "__main__":
    main()
