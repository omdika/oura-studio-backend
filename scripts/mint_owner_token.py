"""One-off CLI to mint a manual JWT for the single owner account.

Temporary bridge until Google Sign-In (POST /auth/google) is fully wired up
(needs GOOGLE_CLIENT_ID + a GCP OAuth client, see handoff Section 6). Once that's
live, tokens should come from /auth/google instead of this script.

Usage:
    python -m scripts.mint_owner_token <owner_email> [--days 30]

Requires SUPABASE_DB_URL and JWT_SECRET in your environment/.env to match the
target deployment (local .env for local dev, or pass the values inline via env
vars if minting a token for Cloud Run -- see README note below).
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from jose import jwt

from app.config import settings
from app.database import SessionLocal
from app.models.owner import OwnerAccount


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("email", help="Owner's email (matches AUTHORIZED_OWNER_EMAIL)")
    parser.add_argument("--days", type=int, default=30, help="Token validity in days (default 30)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        owner = db.query(OwnerAccount).filter(OwnerAccount.email == args.email).first()
        if owner is None:
            owner = OwnerAccount(google_sub="manual-provision-temp", email=args.email)
            db.add(owner)
            db.commit()
            db.refresh(owner)
            print(f"Created owner_account row for {args.email}: {owner.id}", file=sys.stderr)
        else:
            print(f"Using existing owner_account row for {args.email}: {owner.id}", file=sys.stderr)

        now = datetime.now(timezone.utc)
        expire = now + timedelta(days=args.days)
        payload = {"sub": str(owner.id), "iat": now, "exp": expire}
        token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

        print(f"Expires at: {expire.isoformat()}", file=sys.stderr)
        print(token)
    finally:
        db.close()


if __name__ == "__main__":
    main()
