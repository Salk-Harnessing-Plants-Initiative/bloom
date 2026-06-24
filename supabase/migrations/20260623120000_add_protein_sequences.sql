-- =============================================================================
-- 20260623120000_add_protein_sequences.sql
--
-- Adds public.protein_sequences: the amino-acid sequence backing each
-- proteins.uid.
--
-- RLS mirrors the writer-aware shape applied to proteins +
-- protein_embeddings_esm2 in 20260622180000:
--   admin_all / agent_read / user_read / writer_{read,insert,update}
-- The sequence ingest path authenticates as bloom_writer, same as the
-- embedding uploader.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, every CREATE POLICY preceded by
-- DROP IF EXISTS, indexes IF NOT EXISTS.
-- =============================================================================

BEGIN;

-- ─── 1. protein_sequences ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.protein_sequences (
  uid        text        PRIMARY KEY REFERENCES public.proteins(uid) ON DELETE CASCADE,
  sequence   text        NOT NULL CHECK (sequence ~ '^[A-Za-z*]+$'),
  seq_length int         GENERATED ALWAYS AS (char_length(sequence)) STORED,
  seq_md5    text        GENERATED ALWAYS AS (md5(sequence))         STORED,
  source     text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS protein_sequences_seq_md5_idx ON public.protein_sequences (seq_md5);

COMMENT ON TABLE public.protein_sequences IS
  'Amino-acid sequence per proteins.uid (1:1). The CHECK constraint restricts sequence to amino-acid letters + stop (*), rejecting binary/whitespace at write time. seq_length and seq_md5 are generated columns kept in lock-step with sequence by postgres.';
COMMENT ON COLUMN public.protein_sequences.source IS
  'Provenance of the sequence (e.g. uniprot, phytozome, or the source filename). Free-form; informational only.';

-- ─── 2. RLS enable + policies ─────────────────────────────────────────────
ALTER TABLE public.protein_sequences ENABLE ROW LEVEL SECURITY;

-- admin: full access
DROP POLICY IF EXISTS admin_all_protein_sequences ON public.protein_sequences;
CREATE POLICY admin_all_protein_sequences
  ON public.protein_sequences FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);

-- agent: read-only
DROP POLICY IF EXISTS agent_read_protein_sequences ON public.protein_sequences;
CREATE POLICY agent_read_protein_sequences
  ON public.protein_sequences FOR SELECT TO bloom_agent USING (true);

-- user: read-only
DROP POLICY IF EXISTS user_read_protein_sequences ON public.protein_sequences;
CREATE POLICY user_read_protein_sequences
  ON public.protein_sequences FOR SELECT TO bloom_user  USING (true);

-- writer: read + insert + update (no delete), matching the proteins +
-- protein_embeddings_esm2 writer policies. The sequence ingest path
-- authenticates as bloom_writer.
DROP POLICY IF EXISTS writer_read_protein_sequences   ON public.protein_sequences;
CREATE POLICY writer_read_protein_sequences
  ON public.protein_sequences FOR SELECT TO bloom_writer USING (true);
DROP POLICY IF EXISTS writer_insert_protein_sequences ON public.protein_sequences;
CREATE POLICY writer_insert_protein_sequences
  ON public.protein_sequences FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_protein_sequences ON public.protein_sequences;
CREATE POLICY writer_update_protein_sequences
  ON public.protein_sequences FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

-- ─── 3. Table-level GRANTs (PostgREST requires both policy AND grant) ──────
GRANT SELECT                 ON public.protein_sequences TO bloom_user, bloom_agent;
GRANT SELECT, INSERT, UPDATE ON public.protein_sequences TO bloom_writer;
GRANT ALL                    ON public.protein_sequences TO bloom_admin;

COMMIT;
