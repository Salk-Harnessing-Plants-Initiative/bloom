-- Create chat_threads table for storing conversation metadata per user
-- Thread conversation data is stored in PostgresSaver checkpoint tables,
-- this table stores display metadata (title, timestamps) for the UI.

CREATE TABLE IF NOT EXISTS public.chat_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, thread_id)
);

-- Index for fast lookup by user
CREATE INDEX IF NOT EXISTS idx_chat_threads_user_id ON public.chat_threads(user_id);

-- Enable RLS
ALTER TABLE public.chat_threads ENABLE ROW LEVEL SECURITY;

-- Users can only see their own threads
CREATE POLICY "Users can view own chat threads"
    ON public.chat_threads
    FOR SELECT
    USING (auth.uid() = user_id);

-- Users can create their own threads
CREATE POLICY "Users can create own chat threads"
    ON public.chat_threads
    FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- Users can update their own threads
CREATE POLICY "Users can update own chat threads"
    ON public.chat_threads
    FOR UPDATE
    USING (auth.uid() = user_id);

-- Users can delete their own threads
CREATE POLICY "Users can delete own chat threads"
    ON public.chat_threads
    FOR DELETE
    USING (auth.uid() = user_id);

-- Service role has full access (for backend operations)
CREATE POLICY "Service role full access to chat threads"
    ON public.chat_threads
    FOR ALL
    USING (auth.role() = 'service_role');

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_chat_threads_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_chat_threads_updated_at
    BEFORE UPDATE ON public.chat_threads
    FOR EACH ROW
    EXECUTE FUNCTION update_chat_threads_updated_at();
