#!/bin/sh
set -eu
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv app_password APP_DB_PASSWORD
\getenv n8n_password N8N_DB_PASSWORD
CREATE ROLE lead_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
CREATE ROLE lead_n8n LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'n8n_password';
CREATE DATABASE ai_leads_live OWNER lead_app;
CREATE DATABASE ai_leads_n8n_live OWNER lead_n8n;
REVOKE ALL ON DATABASE ai_leads_live FROM PUBLIC;
REVOKE ALL ON DATABASE ai_leads_n8n_live FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE ai_leads_live TO lead_app;
GRANT CONNECT, TEMPORARY ON DATABASE ai_leads_n8n_live TO lead_n8n;
SQL
