#!/bin/sh
set -eu
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password APP_DB_PASSWORD
\getenv n8n_password N8N_DB_PASSWORD
CREATE ROLE lead_app LOGIN PASSWORD :'app_password';
CREATE ROLE lead_n8n LOGIN PASSWORD :'n8n_password';
CREATE DATABASE ai_leads_demo OWNER lead_app;
CREATE DATABASE ai_leads_n8n OWNER lead_n8n;
REVOKE CONNECT ON DATABASE ai_leads_demo FROM PUBLIC;
REVOKE CONNECT ON DATABASE ai_leads_n8n FROM PUBLIC;
GRANT CONNECT ON DATABASE ai_leads_demo TO lead_app;
GRANT CONNECT ON DATABASE ai_leads_n8n TO lead_n8n;
SQL
