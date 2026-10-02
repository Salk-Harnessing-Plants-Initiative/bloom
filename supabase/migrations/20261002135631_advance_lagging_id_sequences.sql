-- Migration: advance_lagging_id_sequences
-- Created: 2026-10-02
--
-- WHY (bloom#1022): in prod, 21 public tables have an id sequence behind their data.
-- An import or restore kept explicit ids without resetting sequences, so an insert
-- that lets the database pick the id can draw one that is already taken. Prod's first
-- pipeline write-back hit this.
--
-- WHAT: the sequence-advance body below. For every sequence-backed column in public
-- (found through pg_get_serial_sequence, never a list) it advances exactly the
-- sequences that are behind -- max(col) >= the value the sequence hands out next -- to
-- setval(seq, max(col), true). A sequence that is not behind is never touched, so none
-- ever moves backwards and re-applying changes nothing.
--
-- setval is not transactional, so the body works in stages and every failure comes
-- before the first setval: (1) find the behind set and check each member can be
-- advanced correctly, (2) lock those tables against writes in one statement, (3)
-- re-check under the lock, (4) setval and NOTICE. It fails loudly rather than skip a
-- sequence it can't advance (openspec/changes/fix-prod-sequences-behind, design D3).
--
-- The DO block is copied verbatim to scripts/sql/advance_behind_sequences.sql and to
-- any later *_readvance_id_sequences_<reason>.sql migration; a unit test pins them.
--
-- Forward-only. Manual rollback (staging hot-apply only):
--   supabase/rollbacks/20261002135631_advance_lagging_id_sequences_rollback.sql

BEGIN;
SET LOCAL lock_timeout = '5s';

DO $advance$
DECLARE
  r record;
  v_pass integer;
  v_max numeric;
  v_last numeric;
  v_called boolean;
  v_inc numeric;
  v_seqmax numeric;
  v_next numeric;
  v_owner text;
  v_visited integer;
  v_locked text[] := '{}';
  v_seqs text[];
  v_tables text[];
  v_cols text[];
  v_nexts numeric[];
  v_maxes numeric[];
  v_incs numeric[];
BEGIN
  -- Pass 1 finds and checks the behind set, then locks it; pass 2 re-checks under the lock.
  FOR v_pass IN 1..2 LOOP
    v_visited := 0;
    v_seqs := '{}';
    v_tables := '{}';
    v_cols := '{}';
    v_nexts := '{}';
    v_maxes := '{}';
    v_incs := '{}';

    FOR r IN
      SELECT c.relname AS tbl,
             a.attname AS col,
             pg_get_serial_sequence(format('public.%I', c.relname), a.attname) AS seq
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
       WHERE n.nspname = 'public'
         AND c.relkind IN ('r', 'p')
         AND NOT c.relispartition
         AND pg_get_serial_sequence(format('public.%I', c.relname), a.attname) IS NOT NULL
       ORDER BY c.relname COLLATE "C", a.attnum
    LOOP
      v_visited := v_visited + 1;

      EXECUTE format('SELECT max(%I)::numeric FROM public.%I', r.col, r.tbl) INTO v_max;
      CONTINUE WHEN v_max IS NULL;

      EXECUTE format('SELECT last_value::numeric, is_called FROM %s', r.seq)
        INTO v_last, v_called;
      SELECT s.seqincrement, s.seqmax INTO v_inc, v_seqmax
        FROM pg_sequence s
       WHERE s.seqrelid = r.seq::regclass;

      -- numeric, so a bigint sequence at its maximum can't overflow here.
      v_next := v_last + (CASE WHEN v_called THEN v_inc ELSE 0 END);
      CONTINUE WHEN v_max < v_next;

      -- Behind. Raise now, before any setval, if it can't be advanced correctly.
      IF NOT has_sequence_privilege(r.seq, 'UPDATE') THEN
        SELECT pg_get_userbyid(relowner) INTO v_owner FROM pg_class WHERE oid = r.seq::regclass;
        RAISE EXCEPTION 'cannot advance %: % lacks UPDATE on it (owner %)',
          r.seq, current_user, v_owner;
      END IF;
      IF NOT has_table_privilege(format('public.%I', r.tbl), 'UPDATE, DELETE, TRUNCATE') THEN
        SELECT pg_get_userbyid(relowner) INTO v_owner
          FROM pg_class WHERE oid = format('public.%I', r.tbl)::regclass;
        RAISE EXCEPTION 'cannot lock public.%: % lacks UPDATE, DELETE and TRUNCATE on it (owner %)',
          quote_ident(r.tbl), current_user, v_owner;
      END IF;
      IF v_inc < 0 THEN
        RAISE EXCEPTION 'cannot advance %: its increment is %', r.seq, v_inc;
      END IF;
      IF v_max + v_inc > v_seqmax THEN
        RAISE EXCEPTION 'cannot advance %: max(%) = % plus increment % passes its maximum %',
          r.seq, quote_ident(r.col), v_max, v_inc, v_seqmax;
      END IF;
      IF v_pass = 2 AND NOT (r.tbl = ANY (v_locked)) THEN
        RAISE EXCEPTION 'public.% became behind after the locks were taken; re-run the migration',
          quote_ident(r.tbl);
      END IF;

      v_seqs := v_seqs || r.seq;
      v_tables := v_tables || r.tbl::text;
      v_cols := v_cols || r.col::text;
      v_nexts := v_nexts || v_next;
      v_maxes := v_maxes || v_max;
      v_incs := v_incs || v_inc;
    END LOOP;

    IF v_pass = 1 THEN
      SELECT coalesce(array_agg(DISTINCT t), '{}') INTO v_locked FROM unnest(v_tables) AS t;
      IF cardinality(v_locked) > 0 THEN
        EXECUTE 'LOCK TABLE '
          || (SELECT string_agg(format('public.%I', t), ', ') FROM unnest(v_locked) AS t)
          || ' IN SHARE ROW EXCLUSIVE MODE';
      END IF;
    END IF;
  END LOOP;

  -- Every check has passed under the lock: advance.
  FOR i IN 1..cardinality(v_seqs) LOOP
    PERFORM setval(v_seqs[i]::regclass, v_maxes[i]::bigint, true);
    RAISE NOTICE 'advanced % for public.%.%: next value % -> % (max = %)',
      v_seqs[i], quote_ident(v_tables[i]), quote_ident(v_cols[i]),
      v_nexts[i], v_maxes[i] + v_incs[i], v_maxes[i];
  END LOOP;

  RAISE NOTICE 'advance_behind_sequences: % of % sequences advanced',
    cardinality(v_seqs), v_visited;
END
$advance$;

COMMIT;
