"""Grant or revoke the admin role.

    python scripts/make_admin.py <email>
    python scripts/make_admin.py <email> --revoke

Deliberately a script and not a screen. The role exists for one thing —
clearing a second factor for somebody who has lost the authenticator *and*
every recovery code — and an account that can do that should be created by
somebody with database credentials, not by clicking. There is no bootstrap
endpoint for the same reason: the first admin has to come from outside the
application, or the application is the thing that grants the privilege.

The role grants nothing over clinical data. Reach comes only from `Access`, and
this does not touch it: an admin can no more read a record than any other
account can.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import close_db, init_db
from app.models.user import User


async def main(email: str, revoke: bool) -> int:
    await init_db()
    try:
        user = await User.find_one(User.email == email.lower())
        if user is None:
            print(f"No account for {email}")
            return 1

        wanted = "user" if revoke else "admin"
        if user.role == wanted:
            print(f"{user.email} is already {wanted}")
            return 0

        was, user.role = user.role, wanted
        await user.save()
        print(f"{user.email}: {was} -> {user.role}")
        return 0
    finally:
        await close_db()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("email")
    p.add_argument("--revoke", action="store_true", help="demote back to user")
    args = p.parse_args()
    raise SystemExit(asyncio.run(main(args.email, args.revoke)))
