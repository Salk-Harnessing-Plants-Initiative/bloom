
GRANT USAGE ON SCHEMA storage TO bloom_user, bloom_admin, bloom_agent, bloom_writer, bloom_workflows;
GRANT USAGE ON SCHEMA auth TO bloom_writer;

-- The video generation queue, owned by bloom_video_queue_owner. Skipped when the queue
-- is absent, so this file stays applicable on any database in any state.
GRANT USAGE ON SCHEMA pgmq TO bloom_video_queue_owner;
DO $$
BEGIN
  IF to_regclass('pgmq.q_cyl_video_generation') IS NOT NULL THEN
    ALTER TABLE pgmq.q_cyl_video_generation OWNER TO bloom_video_queue_owner;
    ALTER TABLE pgmq.a_cyl_video_generation OWNER TO bloom_video_queue_owner;
  END IF;
END
$$;
