# Supabase

Reference for how the Supabase stack is configured inside the bloom monorepo: roles, storage buckets, RLS conventions, JWT auth flow, and a few sharp edges worth knowing before you make changes. Update this file when you change something durable about the schema, the roles, or the storage layer.

## Stack shape

Supabase is run as a self-hosted set of containers under
`docker-compose.prod.yml` (used for both prod and staging, with different
`-p` projects). The components used in this repo:

| Container               | Image                         | What it does                                                                        |
| ----------------------- | ----------------------------- | ----------------------------------------------------------------------------------- |
| `db-prod`               | `supabase/postgres:15.x`      | The Postgres database (data + auth schemas + storage schema).                       |
| `kong`                  | `kong:2.8.1`                  | API gateway. Routes `/auth`, `/rest`, `/storage`, `/realtime` to the right backend. |
| `auth` (gotrue)         | `supabase/gotrue:v2.x`        | User authentication, magic links, JWT issuance.                                     |
| `rest` (postgrest)      | `postgrest/postgrest:v12.x`   | Exposes Postgres tables as REST via the JWT-derived role.                           |
| `storage` (storage-api) | `supabase/storage-api:v1.x`   | Object storage HTTP API in front of MinIO.                                          |
| `realtime`              | `supabase/realtime:v2.x`      | Postgres logical replication → WebSocket.                                           |
| `supavisor`             | `supabase/supavisor:2.x`      | Connection pooler.                                                                  |
| `supabase-minio`        | `minio/minio`                 | S3-compatible object store backing `storage`.                                       |
| `meta`                  | `supabase/postgres-meta:v0.x` | Used by Studio for schema introspection.                                            |
| `studio`                | `supabase/studio:2026.x`      | Admin UI.                                                                           |

The browser-facing URL is the one in `.env.{prod,staging}.defaults` (`SUPABASE_PUBLIC_URL`). Internal services talk to `http://kong:8000` via the `supanet` Docker network.

## The four bloom\_\* Postgres roles

The repo defines four custom Postgres roles that the storage / REST APIs switch into based on the JWT's `role` claim.

The mapping happens in the custom access token hook (`supabase/migrations/20260519140000_jwt_hook_read_app_meta_data.sql`).
Every JWT signed with a `role: bloom_X` claim is switched into that Postgres role for the lifetime of the request.

Direct DB login is reserved for the `postgres` superuser — documented on Notion Page.

| Role           | Intent                                       | Table grants on `public.*` (today)                                                                                                                                                                                                                                                                                       | Table grants on `storage.objects`                                                                                                                                                                                       | Notes                                                                                                                                        |
| -------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `bloom_admin`  | Break-glass: DDL, migrations, manual cleanup | `ALL` on every table (one `admin_all_*` RLS policy per table, `USING (true) WITH CHECK (true)`)                                                                                                                                                                                                                          | `DELETE, INSERT, SELECT, UPDATE` + global `admin_all_objects (USING true, WITH CHECK true)`                                                                                                                             | Effectively superuser-equivalent for application surfaces. Don't issue JWTs for this role to user-facing code.                               |
| `bloom_user`   | Web app users                                | `SELECT` + `INSERT` everywhere. **No table-level `UPDATE` on `public.*` except `public.experiment_progress_logs`** (the gene-page Progress panel). No-UPDATE by design — INSERT is retained, this is not full read-only (#341; migration `20260624000000_bloom_user_read_only_cleanup.sql`, spec `database-role-grants`) | `INSERT, SELECT, UPDATE` + per-bucket `user_read_*` policies for cyl-images, images, videos, scrna, exp-progress-logs, graviscan-\*, species-illustrations; INSERT/UPDATE only on graviscan-images and graviscan-videos | The role users get when they log in via Supabase Auth. The UPDATE policies on graviscan buckets are missing `WITH CHECK` — see Known Issues. |
| `bloom_writer` | Ingestion / pipeline writers                 | `SELECT/INSERT/UPDATE` on ~57 of 58 public tables (essentially everything)                                                                                                                                                                                                                                               | `INSERT, SELECT, UPDATE` + global `writer_select_objects`, `writer_insert_objects`, `writer_update_objects` — all `USING true` / `WITH CHECK true`                                                                      | "Write anywhere" role.**The code calling it IS the scope, not the DB.** Use only for trusted ingestion paths. Inherits from `bloom_user`.    |
| `bloom_agent`  | LLM agents (langchain-agent, bloommcp)       | `SELECT` only across all public tables (read-only)                                                                                                                                                                                                                                                                       | `SELECT` globally + (after the bloommcp PR) `INSERT, UPDATE` table grants + the bucket-scoped `agent_insert_bloommcp_data` and `agent_update_bloommcp_data` policies for `bloommcp-data` only                           | Read-only**for data tables**. The bloommcp PR carved out a single bucket where the agent can write. No DELETE anywhere.                      |

### Role inheritance

```text
postgres
  ├─ bloom_admin
  ├─ bloom_user
  │   └─ bloom_writer        ← writer inherits user's privileges + its own
  └─ bloom_agent

authenticator
  ├─ bloom_admin
  ├─ bloom_user
  ├─ bloom_writer
  └─ bloom_agent
```

The `authenticator` role is what PostgREST / storage-api connect as. After JWT validation, they `SET ROLE bloom_*` to enter the right scope for the request.

## Storage buckets

`bloom-storage` : The single S3 bucket the Supabase Storage API uses as its backend.

**Every logical bucket below is a prefix inside it.**

| Bucket                                               | What it holds                                                                                                             | Public?        | Notes                                                                                                 |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | -------------- | ----------------------------------------------------------------------------------------------------- |
| `images`                                             | Cylinder phenotyping images                                                                                               | no             | bloom_user has SELECT only.                                                                           |
| `cyl-images`                                         | Cylinder scan images                                                                                                      | no             | bloom_user SELECT.                                                                                    |
| `videos`                                             | Cylinder scan videos                                                                                                      | no             | bloom_user SELECT.                                                                                    |
| `scrna`                                              | scRNA-seq counts JSON                                                                                                     | no             | bloom_user SELECT.                                                                                    |
| `species-illustrations` (hyphen; rename in progress) | Per-species illustration thumbnails                                                                                       | no             | bloom_user SELECT. PR #261 renames this from the legacy `species_illustrations` underscore form.      |
| `experiment-log-images`                              | Images attached to gene-candidate progress logs                                                                           | yes (download) | Anyone can read; only authenticated can write.                                                        |
| `plates-images`                                      | Plate scan thumbnails                                                                                                     | yes (download) |                                                                                                       |
| `plate-blob-storage`                                 | Plate scan large blobs                                                                                                    | yes (download) |                                                                                                       |
| `graviscan-images`                                   | Plate-scanner gravi images                                                                                                | no             | bloom_user INSERT + UPDATE — the only buckets users can write to.                                     |
| `graviscan-videos`                                   | Plate-scanner gravi videos                                                                                                | no             | Same as above.                                                                                        |
| `bloommcp-data` (new)                                | CSV exchange between bloommcp tools and external producers/consumers. Two prefixes:`bloommcp_input/`, `bloommcp_output/`. | no             | Only `bloom_agent` can write, scoped via `agent_insert_bloommcp_data` / `agent_update_bloommcp_data`. |
