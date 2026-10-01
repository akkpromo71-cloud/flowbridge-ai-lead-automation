"""Local synthetic actions for a database explicitly marked as a demo."""

from email.message import EmailMessage

from sqlalchemy import select

from app.communication import ingest_inbound, parse_inbound
from app.models import IntegrationState, Lead, Message


def demo_reply(database, settings, lead_id: str) -> str:
    """Ingest one synthetic reply per accepted outbound message, without network I/O."""
    if settings.mode != "demo":
        raise ValueError("demo_reply_requires_demo_mode")
    with database.session.begin() as db:
        deployment = db.get(IntegrationState, "deployment")
        if not deployment or deployment.detail.get("mode") != "demo":
            raise ValueError("demo_reply_requires_demo_database")
        # Serialize repeated CLI invocations before checking the stable inbound purpose key.
        lead = db.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
        if lead is None:
            raise ValueError("demo_reply_lead_not_found")
        local, separator, domain = lead.email.lower().rpartition("@")
        if not local or not separator or domain != "example.com":
            raise ValueError("demo_reply_requires_synthetic_address")
        outbound = db.scalar(
            select(Message)
            .where(
                Message.lead_id == lead.id,
                Message.direction == "outbound",
                Message.state == "provider_accepted",
                Message.rfc_message_id.is_not(None),
                Message.rfc_message_id != "",
            )
            .order_by(
                Message.provider_accepted_at.desc().nulls_last(),
                Message.created_at.desc(),
                Message.id.desc(),
            )
            .limit(1)
        )
        if outbound is None:
            raise ValueError("demo_reply_requires_accepted_outbound")
        message = EmailMessage()
        message["From"] = lead.email
        message["Subject"] = "Синтетический ответ для демонстрации"
        message["Message-ID"] = f"<demo-reply-{outbound.id}@demo.invalid>"
        message["In-Reply-To"] = outbound.rfc_message_id
        message["References"] = outbound.rfc_message_id
        message.set_content("Спасибо! Готовы обсудить следующий шаг. Это синтетический демо-ответ.")
        content = parse_inbound(message.as_bytes())
        return ingest_inbound(db, f"demo-reply:{outbound.id}", content)
