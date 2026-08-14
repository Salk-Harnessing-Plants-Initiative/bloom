
GRANT USAGE ON SCHEMA storage TO bloom_user, bloom_admin, bloom_agent, bloom_writer, bloom_workflows;
GRANT USAGE ON SCHEMA auth TO bloom_writer;

-- The video generation queue, owned by bloom_video_queue_owner.
GRANT USAGE ON SCHEMA pgmq TO bloom_video_queue_owner;
ALTER TABLE pgmq.q_cyl_video_generation OWNER TO bloom_video_queue_owner;
ALTER TABLE pgmq.a_cyl_video_generation OWNER TO bloom_video_queue_owner;
GRANT EXECUTE ON FUNCTION
  pgmq.send(text, jsonb), pgmq.read(text, integer, integer),
  pgmq.delete(text, bigint), pgmq.archive(text, bigint)
  TO bloom_video_queue_owner;
