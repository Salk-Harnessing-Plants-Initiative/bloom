import Link from "next/link";

export function Breadcrumbs({ runId }: { runId: number }) {
  return (
    <div className="text-sm mb-4 text-stone-400 select-none">
      <Link href="/app/cyl-pipeline-runs" className="hover:underline">
        All cylinder pipeline runs
      </Link>
      &nbsp;▸&nbsp;<span>Run {runId}</span>
    </div>
  );
}
