from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from ngo_homesuite.db.schema import init_db
from ngo_homesuite.domain.identity import Identity
from ngo_homesuite.domain.services import Services
from ngo_homesuite.domain.store import TenantStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicitly migrate a database and optionally provision users")
    parser.add_argument("database", type=Path)
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--username")
    parser.add_argument("--role", choices=["admin", "fundraiser", "viewer"], default="admin")
    parser.add_argument("--admin-username")
    args = parser.parse_args()
    init_db(str(args.database))
    print("Database migration complete")
    if args.username:
        identity = Identity(Services(TenantStore({args.tenant: args.database})))
        actor = None
        if args.admin_username:
            actor = identity.login(args.tenant, args.admin_username, getpass.getpass("Admin password: ")).actor
        user_id = identity.create_user(
            args.tenant, args.username, getpass.getpass("New password (min 10 characters): "),
            args.role, actor=actor,
        )
        print(f"User created: {user_id}")


if __name__ == "__main__":
    main()
