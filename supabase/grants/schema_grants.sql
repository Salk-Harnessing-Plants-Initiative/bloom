
GRANT USAGE ON SCHEMA storage TO bloom_user, bloom_admin, bloom_agent, bloom_writer, bloom_workflows;
GRANT USAGE ON SCHEMA auth TO bloom_writer;

-- The video generation queue, owned by bloom_video_queue_owner. Each statement is skipped
-- when the role or the object it targets is absent.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bloom_video_queue_owner') THEN
    RETURN;
  END IF;

  IF to_regnamespace('pgmq') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA pgmq TO bloom_video_queue_owner;
  END IF;

  IF to_regclass('pgmq.q_cyl_video_generation') IS NOT NULL THEN
    ALTER TABLE pgmq.q_cyl_video_generation OWNER TO bloom_video_queue_owner;
  END IF;

  IF to_regclass('pgmq.a_cyl_video_generation') IS NOT NULL THEN
    ALTER TABLE pgmq.a_cyl_video_generation OWNER TO bloom_video_queue_owner;
  END IF;
END
$$;
