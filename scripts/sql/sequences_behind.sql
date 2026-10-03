-- Id sequences behind their data (bloom#1022, openspec change fix-prod-sequences-behind).
--
-- Read-only. One row per sequence-backed public column whose sequence would hand out an
-- id that is already taken: table, column, max(column) and the sequence's next value.
-- No rows means every sequence is ahead of its data.
--
-- "Behind" is the sequence-advance body's definition (the advance_lagging_id_sequences
-- migration): next value = last_value + increment when called, else last_value; behind
-- when max(column) >= next value. Only ascending sequences; descending ones are never
-- behind. A test pins this query to the body's own decisions.
--
-- It is a single plain SELECT, so it runs inside a read-only transaction (a function,
-- even a pg_temp one, can't be created there). last_value and is_called are read from
-- the sequence itself: pg_sequences.last_value is NULL until a sequence is first used.
--
-- Run by scripts/check_health.py (make check) and by the deploy's "Check id sequences
-- are not behind" step. By hand:
--   psql -X -At -v ON_ERROR_STOP=1 < scripts/sql/sequences_behind.sql

SELECT y.tbl AS table_name,
       y.col AS column_name,
       y.max_id::bigint AS max_id,
       y.next_value::bigint AS next_value
  FROM (
        SELECT x.tbl,
               x.col,
               x.max_id,
               x.last_value + CASE WHEN x.is_called THEN x.inc ELSE 0 END AS next_value
          FROM (
                SELECT c.relname AS tbl,
                       a.attname AS col,
                       ps.seqincrement::numeric AS inc,
                       (pg_catalog.xpath('/row/m/text()', pg_catalog.query_to_xml(
                           pg_catalog.format('SELECT max(%I) AS m FROM public.%I', a.attname, c.relname),
                           false, true, '')))[1]::text::numeric AS max_id,
                       (pg_catalog.xpath('/row/last_value/text()', pg_catalog.query_to_xml(
                           pg_catalog.format('SELECT last_value FROM %s', s.seq),
                           false, true, '')))[1]::text::numeric AS last_value,
                       (pg_catalog.xpath('/row/is_called/text()', pg_catalog.query_to_xml(
                           pg_catalog.format('SELECT is_called FROM %s', s.seq),
                           false, true, '')))[1]::text::boolean AS is_called
                  FROM pg_catalog.pg_class c
                  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                  JOIN pg_catalog.pg_attribute a
                    ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                 CROSS JOIN LATERAL (
                        SELECT pg_catalog.pg_get_serial_sequence(
                                   pg_catalog.format('public.%I', c.relname), a.attname) AS seq
                       ) s
                  JOIN pg_catalog.pg_sequence ps ON ps.seqrelid = s.seq::regclass
                 WHERE n.nspname = 'public'
                   AND c.relkind IN ('r', 'p')
                   AND NOT c.relispartition
                   AND s.seq IS NOT NULL
                   AND ps.seqincrement > 0
               ) x
         WHERE x.max_id IS NOT NULL
       ) y
 WHERE y.max_id >= y.next_value
 ORDER BY y.tbl COLLATE "C", y.col;
