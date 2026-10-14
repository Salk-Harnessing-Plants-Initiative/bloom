import { notFound, permanentRedirect } from "next/navigation";
import { parseId } from "@/lib/route-params";

/** A run's former address, still in run e-mails already sent: its page is now /app/rnaseq-runs/<id>. */
export default async function FormerRnaseqRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const id = parseId((await params).runId);
  if (id === null) notFound();
  permanentRedirect(`/app/rnaseq-runs/${id}`);
}
