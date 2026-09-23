-- Seminar profile: enable the three extensions the domain relies on
-- (PostGIS for parcels, TimescaleDB for sensor/weather series, pgvector for the assistant).
-- Verified against timescale/timescaledb-ha:pg16 during E0 (ADR-0003).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
