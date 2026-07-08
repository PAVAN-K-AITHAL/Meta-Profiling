-- =============================================
-- Performance Observatory: Database Schema & Seed Data
-- =============================================
-- This file runs automatically when the PostgreSQL container
-- starts for the first time (via /docker-entrypoint-initdb.d/).
--
-- Contract Reference (Section 1.2 of implementation_plan.md):
--   Connection: postgres://perfobs:perfobs@postgres:5432/perfobs?sslmode=disable
--   Tables:
--     users  (id SERIAL PK, name VARCHAR(100), email VARCHAR(100), created_at TIMESTAMP)
--     orders (id SERIAL PK, user_id INT FK→users, amount DECIMAL(10,2), status VARCHAR(20), created_at TIMESTAMP)

-- =============================================
-- 1. SCHEMA DEFINITION
-- =============================================
-- These table definitions match the database contract exactly.
-- Worker 1's Go code (database/postgres.go) will Scan(&u.ID, &u.Name, &u.Email)
-- so column names and types MUST NOT be changed without notifying all workers.

CREATE TABLE IF NOT EXISTS users (
    id         SERIAL       PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    email      VARCHAR(100) NOT NULL,
    created_at TIMESTAMP    DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id         SERIAL        PRIMARY KEY,
    user_id    INTEGER       REFERENCES users(id),
    amount     DECIMAL(10,2) NOT NULL,
    status     VARCHAR(20)   NOT NULL,
    created_at TIMESTAMP     DEFAULT NOW()
);

-- =============================================
-- 2. SEED DATA — 100,000 Users
-- =============================================
-- Uses generate_series() for fast bulk insertion without
-- needing external CSV files or application-level loops.
-- Each user gets a deterministic name/email pattern:
--   User_1 / user1@example.com
--   User_2 / user2@example.com
--   ...
-- This makes it easy to debug specific rows during testing.

INSERT INTO users (name, email)
SELECT
    'User_' || gs,
    'user' || gs || '@example.com'
FROM generate_series(1, 100000) AS gs;

-- =============================================
-- 3. SEED DATA — 500,000 Orders
-- =============================================
-- Each order is assigned to a random user (1–100000),
-- a random amount ($1–$500), and one of four statuses.
-- The random() distribution means some users will have
-- more orders than others — realistic for load testing.
--
-- Note: random() is non-deterministic, so each fresh
-- docker-compose up (after volume deletion) produces
-- different data. This is acceptable per the plan.

INSERT INTO orders (user_id, amount, status)
SELECT
    (random() * 99999 + 1)::int,
    (random() * 500 + 1)::decimal(10,2),
    (ARRAY['pending','completed','shipped','cancelled'])[floor(random()*4+1)::int]
FROM generate_series(1, 500000);

-- =============================================
-- 4. INDEXES
-- =============================================
-- Without indexes, PostgreSQL would use sequential scans
-- on all queries. These indexes ensure the query planner
-- behaves like a real production database under load.
--
-- idx_orders_user_id: Speeds up JOIN between orders and users
--   (used when the Go API fetches orders for a user)
-- idx_orders_status: Speeds up WHERE status = 'pending' etc.
--   (useful for filtered queries and dashboard metrics)
-- idx_users_email: Speeds up user lookup by email
--   (common pattern in real applications)

CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status  ON orders(status);
CREATE INDEX idx_users_email    ON users(email);

-- =============================================
-- 5. ENABLE pg_stat_statements EXTENSION
-- =============================================
-- pg_stat_statements tracks execution statistics for all
-- SQL statements. The postgres-exporter (Worker 4) reads
-- these stats and exposes them as Prometheus metrics.
-- Without this, we can't see query-level performance in Grafana.
--
-- Requires shared_preload_libraries = 'pg_stat_statements'
-- in postgresql.conf (also created by Worker 2).

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- =============================================
-- 6. UPDATE PLANNER STATISTICS
-- =============================================
-- After bulk inserting 600K rows, PostgreSQL's internal
-- statistics (used by the query planner to choose between
-- index scan vs sequential scan) are stale.
-- ANALYZE forces an immediate statistics refresh so the
-- very first queries after startup get optimal plans.

ANALYZE users;
ANALYZE orders;
