-- Fix RLS policies to filter out soft-deleted threads.
-- Without this, soft-deleted threads are still visible via PostgREST/Supabase client.

-- Drop and recreate SELECT policy with deleted_at filter
DROP POLICY IF EXISTS "Users can view own chat threads" ON public.chat_threads;
CREATE POLICY "Users can view own chat threads"
    ON public.chat_threads
    FOR SELECT
    USING (auth.uid() = user_id AND deleted_at IS NULL);

-- Update UPDATE policy to prevent modifying soft-deleted threads
DROP POLICY IF EXISTS "Users can update own chat threads" ON public.chat_threads;
CREATE POLICY "Users can update own chat threads"
    ON public.chat_threads
    FOR UPDATE
    USING (auth.uid() = user_id AND deleted_at IS NULL);

-- Update DELETE policy to only allow deleting non-deleted threads
DROP POLICY IF EXISTS "Users can delete own chat threads" ON public.chat_threads;
CREATE POLICY "Users can delete own chat threads"
    ON public.chat_threads
    FOR DELETE
    USING (auth.uid() = user_id AND deleted_at IS NULL);

-- Replace composite index with partial index for better performance
DROP INDEX IF EXISTS idx_chat_threads_user_deleted;
CREATE INDEX IF NOT EXISTS idx_chat_threads_active_user
    ON public.chat_threads(user_id) WHERE deleted_at IS NULL;