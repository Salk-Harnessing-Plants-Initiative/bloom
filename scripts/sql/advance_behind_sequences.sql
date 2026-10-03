-- The sequence-advance body (bloom#1022, openspec change fix-prod-sequences-behind).
--
-- Advances every ascending public id sequence that is behind its column's data to
-- setval(seq, max(col), true), and never moves one backwards; a re-run changes nothing.
-- It checks and locks every behind table before advancing any, so a run that can't
-- finish changes nothing. NOTICEs name each sequence it advances.
--
-- The DO block below is a byte-for-byte copy of the one in
-- supabase/migrations/20261002135631_advance_lagging_id_sequences.sql;
-- tests/unit/test_advance_body_copies.py fails if they differ. Change the migration's
-- block only through a new *_readvance_id_sequences_<reason>.sql migration, and copy it
-- here.
--
-- For dev data loaded with explicit ids (e.g. `make seed-gravi` runs this after its
-- seed). Never as a hand fix on prod or staging: there, a behind sequence is fixed by a
-- re-advance migration through the normal deploy.
--   docker compose -f docker-compose.dev.yml exec -T db-dev \
--     psql -U supabase_admin -d postgres -v ON_ERROR_STOP=1 < scripts/sql/advance_behind_sequences.sql

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
  v_saved_path text := current_setting('search_path');
BEGIN
  PERFORM set_config('search_path', 'pg_catalog, pg_temp', true);

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

      SELECT s.seqincrement, s.seqmax INTO v_inc, v_seqmax
        FROM pg_sequence s
       WHERE s.seqrelid = r.seq::regclass;
      -- Descending sequences count down from their start; "behind" doesn't apply to them.
      -- Skipped before reading anything, so they need no privileges.
      CONTINUE WHEN v_inc < 0;

      EXECUTE format('SELECT max(%I)::numeric FROM public.%I', r.col, r.tbl) INTO v_max;
      CONTINUE WHEN v_max IS NULL;

      EXECUTE format('SELECT last_value::numeric, is_called FROM %s', r.seq)
        INTO v_last, v_called;

      -- numeric, so a bigint sequence at its maximum can't overflow here.
      v_next := v_last + (CASE WHEN v_called THEN v_inc ELSE 0 END);
      CONTINUE WHEN v_max < v_next;

      -- Behind. Raise now, before any setval, if it can't be advanced correctly.
      IF v_pass = 2 AND NOT (r.tbl = ANY (v_locked)) THEN
        RAISE EXCEPTION 'public.% became behind after the locks were taken; re-run the migration',
          quote_ident(r.tbl);
      END IF;
      IF NOT has_sequence_privilege(r.seq, 'UPDATE') THEN
        SELECT pg_get_userbyid(relowner) INTO v_owner FROM pg_class WHERE oid = r.seq::regclass;
        RAISE EXCEPTION 'cannot advance %: % lacks UPDATE on it (owner %)',
          r.seq, current_user, v_owner;
      END IF;
      IF NOT has_table_privilege(format('public.%I', r.tbl), 'UPDATE, DELETE, TRUNCATE') THEN
        SELECT pg_get_userbyid(relowner) INTO v_owner
          FROM pg_class WHERE oid = format('public.%I', r.tbl)::regclass;
        RAISE EXCEPTION 'cannot lock public.%: % lacks all of UPDATE, DELETE and TRUNCATE on it (owner %)',
          quote_ident(r.tbl), current_user, v_owner;
      END IF;
      IF v_max + v_inc > v_seqmax THEN
        RAISE EXCEPTION 'cannot advance %: max(%) = % plus increment % passes its maximum %',
          r.seq, quote_ident(r.col), v_max, v_inc, v_seqmax;
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

  PERFORM set_config('search_path', v_saved_path, true);
END
$advance$;

COMMIT;
