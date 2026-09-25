-- Run once against the shared cell database after the stack is up.
-- The database is not publicly reachable. Use the RDS Data API from
-- docs/saas/cloudformation.md. Aurora does not enable pgvector by itself.
CREATE EXTENSION IF NOT EXISTS vector;
