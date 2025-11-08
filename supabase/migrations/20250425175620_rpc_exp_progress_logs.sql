-- RPC to append a log to experiment_progress_logs

create or replace function append_experiment_log(
  gene_id text,
  new_log jsonb
)
returns void
language sql
as $$
  update gene_candidates
  set experiment_progress_logs = coalesce(experiment_progress_logs, '[]'::jsonb) || new_log
  where gene = gene_id;
$$;
