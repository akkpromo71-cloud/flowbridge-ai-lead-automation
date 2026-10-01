import argparse
import getpass
import json
import os
import secrets
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.db import Database
from app.deployment import ensure_database_mode
from app.models import Operator
from app.security import password_hasher
from app.settings import ROOT, Settings


def main():
    parser = argparse.ArgumentParser(
        description="Project-local administration; no default passwords"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-env")
    sub.add_parser("seed-demo")
    sub.add_parser("init-demo-operator")
    reply = sub.add_parser("demo-reply")
    reply.add_argument("--lead-id", required=True, type=UUID)
    create = sub.add_parser("create-operator")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="Оператор")
    args = parser.parse_args()
    if args.command == "init-env":
        target = ROOT / ".env"
        if target.exists():
            raise SystemExit(".env already exists; it was not changed")
        content = (ROOT / ".env.example").read_text(encoding="utf-8")
        content = content.replace(
            "INTERNAL_TOKEN=\n", f"INTERNAL_TOKEN={secrets.token_urlsafe(32)}\n"
        )
        content = content.replace(
            "N8N_WEBHOOK_TOKEN=\n", f"N8N_WEBHOOK_TOKEN={secrets.token_urlsafe(32)}\n"
        )
        with target.open("x", encoding="utf-8") as file:
            file.write(content)
        print("Created local demo .env; credentials are not printed.")
        return
    if args.command == "demo-reply":
        from app.demo import demo_reply

        settings = Settings()
        try:
            demo_reply(Database(settings.database_url), settings, str(args.lead_id))
        except ValueError as error:
            raise SystemExit(str(error)) from None
        return
    if args.command in {"seed-demo", "init-demo-operator"}:
        settings = Settings()
        if settings.mode != "demo":
            raise SystemExit("This command only works in a physically isolated demo deployment")
        database = Database(settings.database_url)
        ensure_database_mode(database, settings.mode)
        if args.command == "seed-demo":
            from app.domain.config import load_business_config
            from app.domain.examples import demo_scenarios
            from app.intake import accept_lead
            from app.schemas import LeadInput

            config = load_business_config(settings.business_config)
            for index, scenario in enumerate(demo_scenarios(config), 1):
                payload = LeadInput(
                    name=f"Демо-клиент {index}",
                    email=f"demo{index}@example.com",
                    company="Синтетическая компания",
                    message=scenario["message"],
                )
                accept_lead(
                    database, config, payload, f"demo-seed:{config.version}:{scenario['id']}"
                )
            print("Synthetic scenarios saved. Start the worker and n8n to process them.")
            return
        email = "demo.operator@example.com"
        credential_root = (
            Path(os.environ.get("LOCALAPPDATA", str(ROOT / ".local"))) / "AILeadAutomationPro"
        )
        credential_root.mkdir(parents=True, exist_ok=True)
        credential_path = credential_root / "demo-operator.json"
        with database.session.begin() as db:
            if db.scalar(select(Operator).where(Operator.email == email)):
                print("Demo operator already exists; password was not changed.")
                return
            password = secrets.token_urlsafe(24)
            with credential_path.open("x", encoding="utf-8") as file:
                json.dump(
                    {"email": email, "password": password, "purpose": "private-local-demo"},
                    file,
                    ensure_ascii=False,
                )
            db.add(
                Operator(
                    email=email,
                    display_name="Демо-оператор",
                    password_hash=password_hasher.hash(password),
                )
            )
        print(f"Private demo credentials saved locally: {credential_path}")
        return
    settings = Settings()
    database = Database(settings.database_url)
    ensure_database_mode(database, settings.mode)
    password = getpass.getpass("Новый пароль (не менее 12 символов): ")
    if len(password) < 12 or password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Password mismatch or too short")
    with database.session.begin() as db:
        if db.scalar(select(Operator).where(Operator.email == args.email.lower())):
            raise SystemExit("Operator already exists")
        db.add(
            Operator(
                email=args.email.lower(),
                display_name=args.name,
                password_hash=password_hasher.hash(password),
            )
        )
    print("Operator created.")


if __name__ == "__main__":
    main()
