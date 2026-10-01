"""Bounded email/Telegram adapters and persisted communication rules.

No network call executes within a database transaction. The final send gate is
the defined linearization point; IMAP and SMTP have no shared transaction.
"""

import email
import imaplib
import re
import smtplib
import ssl
from datetime import timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default
from email.utils import parseaddr

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.admin import cancel_followup, locked_lead
from app.intake import canonical_hash
from app.models import (
    Audit,
    IntegrationState,
    Job,
    Lead,
    Message,
    MessageVersion,
    Suppression,
    new_id,
    utcnow,
)

MAX_INBOUND_BYTES = 2_000_000
MAILBOX_PROBLEM_CODES = (
    "imap_message_too_large",
    "imap_size_unverified",
    "imap_message_size_mismatch",
)


class CommunicationError(RuntimeError):
    def __init__(self, code, *, retryable=False, unknown=False):
        self.code, self.retryable, self.unknown = code, retryable, unknown
        super().__init__(code)


def unresolved_mailbox_problem(db):
    return (
        db.scalar(
            select(Message.id)
            .where(
                Message.direction == "inbound",
                Message.state == "unmatched",
                Message.reply_headers["problem"].astext.in_(MAILBOX_PROBLEM_CODES),
            )
            .limit(1)
        )
        is not None
    )


def inbox_fresh(db, config, now=None):
    state = db.get(IntegrationState, "imap")
    now = now or utcnow()
    return bool(
        state
        and state.state in {"ready", "simulated"}
        and state.last_success_at
        and state.last_success_at
        >= now - timedelta(minutes=config.followup.mailbox_freshness_minutes)
        and not unresolved_mailbox_problem(db)
    )


def blocked_reason(db, lead, message=None):
    if lead.communication_state != "allowed" or db.get(Suppression, lead.email):
        return "communication_stopped"
    if lead.sales_stage in {"won", "lost"}:
        return "lead_closed"
    if message and message.kind == "followup":
        if lead.replied_at or lead.sales_stage == "replied":
            return "client_replied"
        if lead.followup_status in {"cancelled", "completed", "needs_review"}:
            return "followup_stopped"
    return None


def smtp_send(settings, envelope):
    if settings.mode != "live":
        return {"provider": "fake", "accepted": True}
    if not settings.allow_external_sends:
        raise CommunicationError("external_sends_disabled")
    if not all(
        (
            settings.smtp_host,
            settings.smtp_from,
            settings.smtp_username,
            settings.smtp_password.get_secret_value(),
        )
    ):
        raise CommunicationError("smtp_not_configured")
    message = EmailMessage()
    message["From"], message["To"] = settings.smtp_from, envelope["recipient"]
    message["Subject"], message["Message-ID"] = envelope["subject"], envelope["message_id"]
    if envelope.get("in_reply_to"):
        message["In-Reply-To"] = envelope["in_reply_to"]
        message["References"] = envelope["in_reply_to"]
    message.set_content(envelope["body"])
    submission_started = False
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=25) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            submission_started = True
            refused = server.send_message(
                message, from_addr=settings.smtp_from, to_addrs=[envelope["recipient"]]
            )
            if refused:
                raise CommunicationError("smtp_recipient_rejected")
        return {"provider": "smtp", "accepted": True}
    except CommunicationError:
        raise
    except smtplib.SMTPRecipientsRefused as exc:
        # send_message has exactly one envelope recipient. Only a complete,
        # matching RCPT refusal proves that this attempt was not accepted.
        refusals = exc.recipients
        refusal = (
            refusals.get(envelope["recipient"])
            if isinstance(refusals, dict) and set(refusals) == {envelope["recipient"]}
            else None
        )
        if (
            not isinstance(refusal, (tuple, list))
            or len(refusal) != 2
            or type(refusal[0]) is not int
            or not 400 <= refusal[0] < 600
            or not isinstance(refusal[1], (bytes, str))
        ):
            raise CommunicationError("smtp_delivery_unknown", unknown=True) from None
        raise CommunicationError("smtp_rejected", retryable=refusal[0] < 500) from None
    except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
        code = getattr(exc, "smtp_code", 550)
        raise CommunicationError("smtp_rejected", retryable=400 <= code < 500) from None
    except (smtplib.SMTPException, OSError, TimeoutError):
        raise CommunicationError(
            "smtp_delivery_unknown" if submission_started else "smtp_connection",
            unknown=submission_started,
            retryable=not submission_started,
        ) from None


def notification_text(settings, data):
    """Plain text, configured protected URL, no tokens or client-message payload."""
    if isinstance(data, str):
        return f"Приоритетная заявка. Откройте защищённую карточку: {settings.public_url}/#/app/leads/{data}"

    def clean(value, limit):
        return " ".join(str(value or "—").split())[:limit]

    return "\n".join(
        [
            f"{clean(data.get('temperature'), 8)} · {clean(data.get('score'), 3)}/100",
            f"Имя: {clean(data.get('name'), 120)}",
            f"Компания: {clean(data.get('company'), 160)}",
            f"Услуга: {clean(data.get('service'), 100)}",
            f"Срочность: {clean(data.get('urgency'), 30)}",
            f"Кратко: {clean(data.get('summary'), 500)}",
            f"Защищённая карточка: {settings.public_url.rstrip('/')}/#/app/leads/{data['lead_id']}",
        ]
    )


def telegram_notify(settings, data):
    if settings.mode != "live":
        return {"provider": "fake", "accepted": True}
    if not settings.allow_external_sends:
        raise CommunicationError("external_sends_disabled")
    if not settings.telegram_bot_token.get_secret_value() or not settings.telegram_chat_id:
        raise CommunicationError("telegram_not_configured")
    url = (
        f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/sendMessage"
    )
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": notification_text(settings, data),
                    "link_preview_options": {"is_disabled": True},
                },
            )
        if response.status_code == 429:
            raise CommunicationError("telegram_rate_limit", retryable=True)
        if response.status_code >= 500:
            raise CommunicationError("telegram_delivery_unknown", unknown=True)
        if response.status_code != 200 or not response.json().get("ok"):
            raise CommunicationError("telegram_rejected")
        return {"provider": "telegram", "message_id": response.json()["result"]["message_id"]}
    except (httpx.RequestError, ValueError):
        # Do not log the exception: Telegram's URL contains its credential.
        raise CommunicationError("telegram_delivery_unknown", unknown=True) from None


def prepare_send(database, settings, config, job):
    # A synchronization failure must block follow-up, not become "no reply".
    with database.session() as db:
        msg = db.get(Message, job.message_id)
        followup = bool(msg and msg.kind == "followup")
    if followup:
        sync_mailbox(database, settings)
    with database.session.begin() as db:
        current_job = db.scalar(select(Job).where(Job.id == job.id).with_for_update())
        if (
            not current_job
            or current_job.generation != job.generation
            or current_job.status != "running"
            or not current_job.lease_until
            or current_job.lease_until <= utcnow()
        ):
            raise CommunicationError("stale_send_attempt")
        lead = locked_lead(db, job.lead_id)
        msg = db.scalar(select(Message).where(Message.id == job.message_id).with_for_update())
        if not msg:
            raise CommunicationError("message_missing")
        if msg.state == "provider_accepted":
            return {"already_accepted": True}
        if msg.state in {"sending", "delivery_unknown"}:
            raise CommunicationError("smtp_delivery_unknown", unknown=True)
        version = db.get(MessageVersion, msg.current_version_id) if msg.current_version_id else None
        reason = blocked_reason(db, lead, msg)
        # Each send job is bound to the exact approved revision, not just the message.
        if (
            msg.state != "queued"
            or not version
            or msg.approved_version_id != version.id
            or job.dedup_key != f"send:{msg.id}:{version.id}"
        ):
            reason = "approval_not_current"
        if followup and not inbox_fresh(db, config):
            reason = "inbox_not_fresh"
        if reason:
            raise CommunicationError(reason)
        if settings.mode == "live" and not settings.allow_external_sends:
            raise CommunicationError("external_sends_disabled")
        domain = (
            settings.smtp_from.rsplit("@", 1)[-1] if settings.mode == "live" else "demo.invalid"
        )
        msg.rfc_message_id = msg.rfc_message_id or f"<{new_id()}@{domain}>"
        msg.state, msg.send_started_at = "sending", utcnow()
        parent = db.get(Message, msg.parent_message_id) if msg.parent_message_id else None
        return dict(
            recipient=version.recipient,
            subject=version.subject,
            body=version.body,
            message_id=msg.rfc_message_id,
            in_reply_to=parent.rfc_message_id if parent else None,
        )


def record_accepted(db, job, config):
    lead = locked_lead(db, job.lead_id)
    msg = db.scalar(select(Message).where(Message.id == job.message_id).with_for_update())
    if msg.state == "provider_accepted":
        return
    msg.state, msg.provider_accepted_at = "provider_accepted", utcnow()
    if lead.sales_stage == "new":
        lead.sales_stage = "contacted"
    if msg.kind == "initial" and not blocked_reason(db, lead) and not lead.replied_at:
        lead.followup_due_at = msg.provider_accepted_at + timedelta(
            hours=config.followup.delay_hours
        )
        lead.followup_status = "scheduled"
    elif msg.kind == "followup":
        lead.followup_status = "completed"
    lead.version += 1
    db.add(
        Audit(
            lead_id=lead.id,
            kind="message.provider_accepted",
            actor="system",
            detail={"message_id": msg.id},
        )
    )


def message_failure(db, job, code, unknown, retryable):
    if not job.message_id:
        return
    locked_lead(db, job.lead_id)
    msg = db.scalar(select(Message).where(Message.id == job.message_id).with_for_update())
    if not msg or msg.state == "provider_accepted":
        return
    # Editing/rejection that happened before the gate must remain authoritative.
    if job.dedup_key == f"send:{msg.id}:{msg.current_version_id}" and msg.state in {
        "queued",
        "sending",
    }:
        msg.state = "delivery_unknown" if unknown else ("queued" if retryable else "failed")
    db.add(
        Audit(
            lead_id=job.lead_id,
            kind="message.error",
            actor="system",
            detail={"message_id": msg.id, "code": code},
        )
    )


def check_followups(database, config, now=None):
    now = now or utcnow()
    prepared, review = 0, 0
    with database.session.begin() as db:
        due = db.scalars(
            select(Lead)
            .where(Lead.followup_status == "scheduled", Lead.followup_due_at <= now)
            .with_for_update(skip_locked=True)
        ).all()
        for lead in due:
            if blocked_reason(db, lead) or lead.replied_at:
                cancel_followup(db, lead, "eligibility_changed")
                continue
            if not inbox_fresh(db, config, now):
                lead.followup_status = "needs_review"
                db.add(
                    Audit(
                        lead_id=lead.id,
                        kind="followup.manual_check",
                        actor="system",
                        detail={"reason": "inbox_not_fresh"},
                    )
                )
                review += 1
                continue
            created = db.execute(
                insert(Job)
                .values(
                    kind="followup_prepare",
                    lead_id=lead.id,
                    dedup_key=f"followup:{lead.id}",
                    config_snapshot=config.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["dedup_key"])
                .returning(Job.id)
            ).scalar_one_or_none()
            if created is None:
                # One follow-up per lead. An old job is not a newly prepared
                # draft; do not resurrect a failed/cancelled request silently.
                lead.followup_status = "needs_review"
                db.add(
                    Audit(
                        lead_id=lead.id,
                        kind="followup.manual_check",
                        actor="system",
                        detail={"reason": "followup_already_requested"},
                    )
                )
                review += 1
                continue
            lead.followup_status = "pending_approval"
            prepared += 1
    return {"prepared": prepared, "needs_review": review}


def _decoded(value):
    return str(make_header(decode_header(str(value or ""))))[:1000]


def parse_inbound(raw: bytes):
    parsed = email.message_from_bytes(raw, policy=default)
    sender = parseaddr(str(parsed.get("From", "")))[1].lower()
    body = ""
    for part in parsed.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/plain":
            body = part.get_content()[:12000]
            break
    if not body:
        body = "[HTML-письмо: автоматическое отображение отключено. Проверьте почтовый ящик.]"
    refs = re.findall(
        r"<[^<>\s]+>", " ".join(str(parsed.get(h, "")) for h in ("In-Reply-To", "References"))
    )
    automated = str(parsed.get("Auto-Submitted", "no")).lower() != "no"
    bounce = parsed.get_content_type() == "multipart/report"
    return {
        "sender": sender,
        "subject": _decoded(parsed.get("Subject"))[:250],
        "body": body,
        "message_id": str(parsed.get("Message-ID", ""))[:512],
        "references": refs[-30:],
        "automated": automated or bounce,
        "category": "bounce" if bounce else ("auto_reply" if automated else "reply"),
    }


def ingest_inbound(db, event_key, content):
    existing = db.scalar(select(Message).where(Message.purpose_key == event_key))
    if existing:
        return existing.id
    matches = (
        db.scalars(
            select(Message).where(
                Message.direction == "outbound", Message.rfc_message_id.in_(content["references"])
            )
        ).all()
        if content["references"]
        else []
    )
    lead_ids = {msg.lead_id for msg in matches if msg.lead_id}
    lead = locked_lead(db, next(iter(lead_ids))) if len(lead_ids) == 1 else None
    if lead and lead.email.lower() != content["sender"]:
        lead = None
    matched = lead is not None and not content.get("automated")
    category = content.get("category", "auto_reply" if content.get("automated") else "reply")
    if category == "reply" and not matched:
        category = "ambiguous" if matches else "unmatched"
    msg = Message(
        id=new_id(),
        lead_id=lead.id if lead else None,
        direction="inbound",
        kind="reply" if matched else "unmatched",
        state="received" if matched else "unmatched",
        purpose_key=event_key,
        rfc_message_id=content["message_id"] or None,
        reply_headers={
            "references": content["references"],
            "sender": content["sender"],
            "category": category,
        },
    )
    db.add(msg)
    db.flush()
    version = MessageVersion(
        id=new_id(),
        message_id=msg.id,
        revision=1,
        recipient=content["sender"],
        subject=content["subject"] or "(Без темы)",
        body=content["body"],
        checksum=canonical_hash(content),
    )
    db.add(version)
    db.flush()
    msg.current_version_id = version.id
    if matched:
        lead.replied_at = utcnow()
        if lead.sales_stage not in {"meeting_booked", "won", "lost"}:
            lead.sales_stage = "replied"
        lead.version += 1
        cancel_followup(db, lead, "client_replied")
        db.add(
            Audit(
                lead_id=lead.id,
                kind="message.received",
                actor="client",
                detail={"message_id": msg.id},
            )
        )
    else:
        # Sender alone cannot thread a message, but uncertainty prevents automatic follow-up.
        for candidate in db.scalars(
            select(Lead)
            .where(
                Lead.email == content["sender"],
                Lead.followup_status.in_(["scheduled", "pending_approval", "queued"]),
            )
            .with_for_update()
        ):
            candidate.followup_status = "needs_review"
            db.add(
                Audit(
                    lead_id=candidate.id,
                    kind="inbox.manual_check",
                    actor="system",
                    detail={"message_id": msg.id},
                )
            )
    return msg.id


def _reported_mail_size(fetched):
    # A SIZE-only request must produce one unambiguous, bounded numeric value.
    # Never download a body just to discover how large it is.
    if not fetched or any(not isinstance(part, bytes) for part in fetched):
        return None
    if sum(len(part) for part in fetched) > 4096:
        return None
    sizes = re.findall(rb"\bRFC822\.SIZE\s+([0-9]{1,20})(?=[\s)])", b" ".join(fetched))
    return int(sizes[0]) if len(sizes) == 1 else None


def _record_mailbox_problem(db, event_key, validity, uid, size, code):
    # Reuse the existing unmatched inbox and manual matching path. No original
    # content or untrusted headers are retained for this unscoped problem.
    size_label = str(size) if size is not None else "не подтверждён"
    message_id = ingest_inbound(
        db,
        event_key,
        {
            "sender": "",
            "subject": "Входящее письмо требует ручной проверки",
            "body": (
                "Содержимое письма не сохранено. Проверьте почтовый ящик вручную. "
                f"Код: {code}; ящик: default; UIDVALIDITY: {validity}; UID: {uid}; "
                f"размер: {size_label} байт. "
                "Follow-up заблокирован до ручного сопоставления ответа с заявкой. "
                "Если связь установить нельзя, оставьте письмо неразобранным."
            ),
            "message_id": "",
            "references": [],
            "automated": True,
        },
    )
    db.get(Message, message_id).reply_headers = {
        "sender": "",
        "references": [],
        "problem": code,
        "mailbox": "default",
        "uidvalidity": validity,
        "uid": uid,
        "size": size,
    }
    # Unknown threading affects the whole mailbox. Persist the suspension now:
    # a quick manual match must not silently restore previous send permissions.
    for lead in db.scalars(
        select(Lead)
        .where(Lead.followup_status.in_(["scheduled", "pending_approval", "queued"]))
        .order_by(Lead.id)
        .with_for_update()
    ):
        lead.followup_status = "needs_review"
        lead.version += 1
        lead.updated_at = utcnow()
        db.add(
            Audit(
                lead_id=lead.id,
                kind="inbox.manual_check",
                actor="system",
                detail={"message_id": message_id, "reason": code},
            )
        )


def sync_mailbox(database, settings):
    if settings.mode != "live":
        with database.session.begin() as db:
            db.execute(
                insert(IntegrationState)
                .values(name="imap", state="simulated", last_success_at=utcnow(), detail={})
                .on_conflict_do_update(
                    index_elements=["name"],
                    set_={"state": "simulated", "last_success_at": utcnow()},
                )
            )
        return {"state": "simulated", "received": 0}
    if not (
        settings.imap_host and settings.imap_username and settings.imap_password.get_secret_value()
    ):
        raise CommunicationError("imap_not_configured")
    # Session-level advisory lock on a dedicated AUTOCOMMIT connection: no open
    # database transaction while IMAP runs, and a second sync cannot advance a stale cursor.
    with database.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(817245)")).scalar():
            raise CommunicationError("imap_sync_busy", retryable=True)
        try:
            with database.session() as db:
                saved = db.get(IntegrationState, "imap")
                checkpoint = dict(saved.detail) if saved else {}
            with imaplib.IMAP4_SSL(
                settings.imap_host,
                settings.imap_port,
                ssl_context=ssl.create_default_context(),
                timeout=25,
            ) as mailbox:
                mailbox.login(settings.imap_username, settings.imap_password.get_secret_value())
                status, _ = mailbox.select(settings.imap_folder, readonly=True)
                if status != "OK":
                    raise CommunicationError("imap_folder")
                validity_data = mailbox.response("UIDVALIDITY")[1]
                validity = int(validity_data[0])
                if checkpoint.get("uidvalidity") not in (None, validity):
                    raise CommunicationError("imap_uidvalidity_changed")
                cursor = checkpoint.get("last_uid", 0)
                status, found = mailbox.uid("search", None, f"UID {cursor + 1}:*")
                if status != "OK":
                    raise CommunicationError("imap_search")
                # A checkpoint may advance only over the ordered processed prefix.
                uids = sorted({int(uid) for uid in found[0].split() if int(uid) > cursor})
                # Bounded catch-up; freshness only after all discovered UIDs are committed.
                for uid in uids[:100]:
                    status, fetched = mailbox.uid("fetch", str(uid), "(RFC822.SIZE)")
                    if status != "OK":
                        raise CommunicationError("imap_fetch")
                    size = _reported_mail_size(fetched)
                    problem = (
                        "imap_size_unverified"
                        if size is None
                        else ("imap_message_too_large" if size > MAX_INBOUND_BYTES else None)
                    )
                    content = None
                    if problem is None:
                        # The extra byte detects a dishonest SIZE without requesting
                        # an unbounded body. A partial/truncated message is never parsed.
                        status, fetched = mailbox.uid(
                            "fetch", str(uid), f"(BODY.PEEK[]<0.{MAX_INBOUND_BYTES + 1}>)"
                        )
                        if status != "OK":
                            raise CommunicationError("imap_fetch")
                        parts = [
                            part[1]
                            for part in fetched
                            if isinstance(part, tuple)
                            and len(part) == 2
                            and isinstance(part[1], bytes)
                        ]
                        raw = parts[0] if len(parts) == 1 else b""
                        if len(raw) > MAX_INBOUND_BYTES:
                            problem = "imap_message_too_large"
                        elif len(parts) != 1 or len(raw) != size:
                            problem = "imap_message_size_mismatch"
                        else:
                            content = parse_inbound(raw)
                    with database.session.begin() as db:
                        event_key = f"imap:default:{validity}:{uid}"
                        if problem:
                            _record_mailbox_problem(db, event_key, validity, uid, size, problem)
                        else:
                            ingest_inbound(db, event_key, content)
                        # Result, follow-up suspension and cursor commit together.
                        db.execute(
                            insert(IntegrationState)
                            .values(
                                name="imap",
                                state="syncing",
                                detail={"uidvalidity": validity, "last_uid": uid},
                            )
                            .on_conflict_do_update(
                                index_elements=["name"],
                                set_={
                                    "state": "syncing",
                                    "detail": {"uidvalidity": validity, "last_uid": uid},
                                },
                            )
                        )
                complete = len(uids) <= 100
                with database.session.begin() as db:
                    needs_review = unresolved_mailbox_problem(db)
                    state = "needs_review" if needs_review else ("ready" if complete else "syncing")
                    values = {
                        "name": "imap",
                        "state": state,
                        "detail": {
                            "uidvalidity": validity,
                            "last_uid": max(uids[:100], default=cursor),
                        },
                    }
                    if complete and not needs_review:
                        values["last_success_at"] = utcnow()
                    db.execute(
                        insert(IntegrationState)
                        .values(**values)
                        .on_conflict_do_update(
                            index_elements=["name"],
                            set_={k: v for k, v in values.items() if k != "name"},
                        )
                    )
                return {
                    "state": state,
                    "received": min(len(uids), 100),
                }
        except (CommunicationError, imaplib.IMAP4.error, OSError, ValueError) as exc:
            code = exc.code if isinstance(exc, CommunicationError) else "imap_connection"
            with database.session.begin() as db:
                db.execute(
                    insert(IntegrationState)
                    .values(name="imap", state="error", detail={})
                    .on_conflict_do_update(index_elements=["name"], set_={"state": "error"})
                )
            raise CommunicationError(code) from None
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(817245)"))
