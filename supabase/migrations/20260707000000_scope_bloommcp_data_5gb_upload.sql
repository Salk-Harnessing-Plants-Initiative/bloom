-- Scope a 5 GB upload ceiling to the bloommcp-data bucket; keep every other
-- bucket at 500 MB.
--
-- The storage service's global FILE_SIZE_LIMIT (docker-compose storage env) is
-- raised to 5 GB in this change so bloommcp can accept GB-scale counts matrices
-- via the signed upload path. That global is a SHARED ceiling for every bucket,
-- so this migration pins every other bucket that still inherits it to 500 MB —
-- preserving today's effective cap rather than letting them inherit 5 GB. A
-- per-bucket limit can only lower a bucket below the global, never raise it, so
-- bloommcp-data is set to the full 5 GB.
--
-- Safe to re-run: the pin only touches buckets that still inherit the global
-- (NULL limit); once set, re-running is a no-op.

-- Every other bucket that still inherits the global -> pin to 500 MB.
UPDATE storage.buckets
  SET file_size_limit = 524288000  -- 500 MB
  WHERE file_size_limit IS NULL AND id <> 'bloommcp-data';

-- bloommcp-data -> 5 GB (effective only while the global FILE_SIZE_LIMIT >= 5 GB).
UPDATE storage.buckets
  SET file_size_limit = 5368709120  -- 5 GB
  WHERE id = 'bloommcp-data';
