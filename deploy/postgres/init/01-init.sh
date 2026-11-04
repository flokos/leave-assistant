#!/usr/bin/env bash
# Runs once on first start of the pgvector/pgvector container (empty data volume).
# Creates application roles (ADR-0003), the logically separate databases (brief §4.4) and extensions.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- application roles (ADR-0003): owner runs migrations; app_rls is the RLS-bound runtime role
    CREATE ROLE app_owner LOGIN PASSWORD '${LEAVE_DB_OWNER_PASSWORD}';
    CREATE ROLE app_rls LOGIN PASSWORD '${LEAVE_DB_PASSWORD}' NOBYPASSRLS;
    CREATE ROLE langgraph_rw LOGIN PASSWORD '${LANGGRAPH_DB_PASSWORD}' NOBYPASSRLS;
    GRANT CREATE, CONNECT ON DATABASE ${POSTGRES_DB} TO app_owner;
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO app_rls, langgraph_rw;

    -- extensions used by the application database (doc 07)
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS unaccent;
    CREATE EXTENSION IF NOT EXISTS pgcrypto;

    -- logically separate databases for the other services (brief §4.4)
    CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
    CREATE DATABASE keycloak OWNER keycloak;
    CREATE ROLE n8n LOGIN PASSWORD '${N8N_DB_PASSWORD}';
    CREATE DATABASE n8n OWNER n8n;
    CREATE ROLE langfuse LOGIN PASSWORD '${LANGFUSE_DB_PASSWORD}';
    CREATE DATABASE langfuse OWNER langfuse;
EOSQL

# PostgreSQL 15+ removed PUBLIC CREATE on schema public; the app schemas are created by app_owner in M1 migrations.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    GRANT USAGE ON SCHEMA public TO app_rls, langgraph_rw;
    GRANT ALL ON SCHEMA public TO app_owner;
EOSQL
