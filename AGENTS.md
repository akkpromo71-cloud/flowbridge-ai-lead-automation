# AI Lead Automation System

## Authority and boundaries
- User-facing communication and documentation: Russian. Code/API names: English.
- Implement the approved single-company v1 only. No SaaS, CRM expansion, billing, RAG, extra channels or autonomous client communication.
- Project-local dependencies and synthetic tests are authorized. System installations/settings, purchases, public deployment, real customer data and external messages require separate user authorization.
- Never touch the existing n8n instance or its data (historically localhost:5679).
- Never claim a live integration is verified from fake tests. Report IMPLEMENTED / VERIFIED / BLOCKED / DEFERRED with evidence.

## Architecture
- FastAPI owns validation, auth, scoring, state transitions, approval and integration adapters.
- PostgreSQL is the only source of application state. Intake and job creation are atomic.
- n8n orchestrates through authenticated internal API; no duplicated business rules or direct application DB writes.
- One bounded jobs/steps mechanism with database claims and generation fencing. No broker or universal workflow engine.
- No database transaction remains open across an AI, SMTP, Telegram or IMAP network call.
- External side effects are not exactly-once. Ambiguous SMTP outcomes are delivery_unknown and cannot auto-resend.
- Approval binds immutable subject/body/recipient version. Editing invalidates send permission.
- service_fit explicitly distinguishes fit/not_fit/unknown; scoring consumes structured validated fields only.
- Demo is a separate process/database without live secrets. Public demo is read-only; interactive demo requires operator auth.

## Quality and safety
- Parameterized SQLAlchemy queries, UTC timestamps, Decimal money, strict schemas, escaped output.
- No secrets, complete messages or personal data in routine logs or workflow exports.
- Meaningful unit tests plus PostgreSQL integration tests; SQLite is not a substitute.
- Run tests with .venv/Scripts/python.exe -m pytest backend/tests (on Windows).
- Run .venv/Scripts/python.exe -m ruff check backend and frontend npm run build/lint.
- Version and commands are verified in docs/verification.md. Keep that report truthful.
- Do not overwrite user changes or delete unknown paths. Verify targets before recursive deletion.
