-- Add soft delete support to chat_threads
-- Instead of permanently deleting threads, we set deleted_at timestamp.
-- Checkpoint data is preserved for potential recovery.

ALTER TABLE public.chat_threads
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ DEFAULT NULL;

-- Index for efficient filtered queries (most queries filter deleted_at IS NULL)
CREATE INDEX IF NOT EXISTS idx_chat_threads_user_deleted
    ON public.chat_threads(user_id, deleted_at);
