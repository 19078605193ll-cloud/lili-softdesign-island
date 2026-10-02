"""Explicit local operator account management; passwords never enter argv."""

import argparse
import getpass
import uuid

from sqlalchemy import delete, select
from app.core.runtime import run_async
from app.core.security import ROLE_PERMISSIONS, passwords
from app.core.identifiers import identifiers_available, normalize_username
from app.core.user_management import status_lock, ensure_can_disable
from app.db import SessionFactory, engine
from app.models import AuditEvent, Role, User, UserRole


async def execute(args, password=None):
    username = normalize_username(args.username)
    async with SessionFactory.begin() as session:
        if args.command == "create":
            await identifiers_available(session, username)
        else:
            await status_lock(session)
        user = await session.scalar(
            select(User)
            .where(User.username == username)
            .with_for_update()
        )
        if args.command == "create":
            if user:
                raise ValueError("Account already exists")
            user = User(
                username=username,
                password_hash=passwords.hash(password),
            )
            session.add(user)
            await session.flush()
        elif not user:
            raise ValueError("Account not found")
        if args.command == "password":
            user.password_hash = passwords.hash(password)
        if args.command == "disable":
            await ensure_can_disable(session, user)
            user.active = False
        if args.command == "enable":
            user.active = True
        if args.command in {"roles", "create"}:
            await session.execute(delete(UserRole).where(UserRole.user_id == user.id))
            for name in args.roles:
                if await session.get(Role, name) is None:
                    session.add(Role(name=name))
                    await session.flush()
                session.add(UserRole(user_id=user.id, role=name))
        if args.command != "create":
            user.auth_version += 1
        session.add(
            AuditEvent(
                user_id=None,
                action="operator:" + args.command,
                object_id=str(user.id),
                request_id=uuid.uuid4().hex,
                summary={"roles": getattr(args, "roles", [])},
            )
        )
    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["create", "password", "roles", "disable", "enable"]
    )
    parser.add_argument("username")
    parser.add_argument(
        "--roles", nargs="*", choices=sorted(ROLE_PERMISSIONS), default=[]
    )
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 100:
        parser.error("Username must contain 1–100 characters")
    password = None
    if args.command in {"create", "password"}:
        password = getpass.getpass("Password: ")
        if not 12 <= len(password) <= 128 or password != getpass.getpass("Confirm: "):
            parser.error("Passwords must match and contain 12–128 characters")
    run_async(execute(args, password))


if __name__ == "__main__":
    main()
