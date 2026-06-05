import Link from "next/link";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

interface SessionRow {
  id: number;
  started_at: string | null;
  total_cycles: number | null;
  duration_seconds: number | null;
  actual_duration_seconds: number | null;
  phenotypers: { first_name: string | null; last_name: string | null } | null;
}

interface ScanRow {
  plate_id: string | null;
}

interface GraviExperimentRow {
  id: number;
  name: string;
  system_name: string | null;
  cyl_scientists: { scientist_name: string | null; email: string | null } | null;
  accessions: { name: string | null } | null;
  gravi_scan_sessions: SessionRow[];
  gravi_scans: ScanRow[];
}

interface SpeciesWithGraviExperimentsFull {
  id: number;
  common_name: string | null;
  gravi_experiments: GraviExperimentRow[];
}

export default async function PlateSpecies({
  params,
}: {
  params: Promise<{ speciesId: string }>;
}) {
  const { speciesId } = await params;
  const species = await getSpeciesWithExperiments(Number(speciesId));

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/plate-phenotypes/${speciesId}`,
  });

  if (
    !species ||
    !species.gravi_experiments ||
    species.gravi_experiments.length === 0
  ) {
    return (
      <div>
        <Breadcrumb commonName={species?.common_name ?? "Unknown species"} />
        <div className="text-neutral-500 italic">
          No plate experiments found for this species.
        </div>
      </div>
    );
  }

  return (
    <div>
      <Breadcrumb commonName={species.common_name ?? ""} />
      <p className="mb-6 max-w-2xl text-sm text-stone-500">
        Plate (gravitropism) experiments on this species. Per-experiment
        drilldown is coming soon.
      </p>

      <div className="table-auto select-none">
        {species.gravi_experiments.map((experiment) => {
          const accessionName = experiment.accessions?.name ?? null;
          const scientistName = experiment.cyl_scientists?.scientist_name ?? null;
          const scientistEmail = experiment.cyl_scientists?.email ?? null;

          const latest = latestSession(experiment.gravi_scan_sessions);
          const scannedOn = formatDate(latest?.started_at);
          const duration = formatDuration(
            latest?.actual_duration_seconds ?? latest?.duration_seconds ?? null,
          );
          // Per-plate timepoint count derived from gravi_scans, matching the
          // experiment detail page (total_cycles from the session config can
          // disagree with the recorded scan count by ±1).
          const timepoints = perPlateTimepointCount(experiment.gravi_scans);
          const sessionCount = experiment.gravi_scan_sessions?.length ?? 0;
          const plateCount = uniquePlateCount(experiment.gravi_scans);
          const phenotypers = uniquePhenotyperNames(
            experiment.gravi_scan_sessions,
          );

          const timeSeries =
            timepoints > 0 && duration
              ? `${timepoints} time point${timepoints === 1 ? "" : "s"} collected over ${duration}`
              : timepoints > 0
                ? `${timepoints} time point${timepoints === 1 ? "" : "s"}`
                : duration
                  ? `Collected over ${duration}`
                  : null;

          const stats = [
            accessionName,
            scannedOn ? `Scanned ${scannedOn}` : null,
            timeSeries,
            `${plateCount} plate${plateCount === 1 ? "" : "s"}`,
            sessionCount > 1 ? `${sessionCount} sessions` : null,
          ]
            .filter(Boolean)
            .join(" · ");

          const people = [
            scientistName ? `Led by ${scientistName}` : null,
            phenotypers.length > 0
              ? `Phenotyped by ${phenotypers.join(", ")}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ");

          return (
            <div className="table-row" key={experiment.id}>
              <div className="table-cell align-middle p-4">
                <div className="text-lg align-middle">
                  <Link
                    href={`/app/plate-phenotypes/${species.id}/${experiment.id}`}
                    className="mr-4"
                  >
                    <span className="text-lime-700 hover:underline">
                      {capitalizeFirstLetter(
                        experiment.name.replaceAll("-", " "),
                      )}
                    </span>
                  </Link>
                  {experiment.system_name && (
                    <span className="ml-1 text-sm font-normal text-stone-400">
                      {experiment.system_name}
                    </span>
                  )}
                  {scientistEmail && (
                    <a
                      href={`mailto:${scientistEmail}`}
                      className="ml-2 inline-block align-middle"
                      title={scientistName ?? scientistEmail}
                    >
                      <EmailIcon />
                    </a>
                  )}
                </div>
                {stats && (
                  <div className="mt-2 text-sm text-neutral-400">{stats}</div>
                )}
                {people && (
                  <div className="mt-0.5 text-sm text-neutral-400">{people}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Breadcrumb({ commonName }: { commonName: string }) {
  return (
    <div className="text-xl mb-8 select-none">
      <span className="text-stone-400">
        <span className="hover:underline">
          <Link href="/app/plate-phenotypes">All species</Link>
        </span>
        &nbsp;▸&nbsp;
      </span>
      <span className="capitalize">{commonName}</span>
    </div>
  );
}

function EmailIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
      className="w-4 h-4 inline-block -mt-1 opacity-50 hover:opacity-100"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75"
      />
    </svg>
  );
}

function capitalizeFirstLetter(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function latestSession(sessions: SessionRow[] | null | undefined): SessionRow | null {
  if (!sessions || sessions.length === 0) return null;
  return sessions
    .filter((s) => s.started_at)
    .sort((a, b) =>
      (b.started_at ?? "").localeCompare(a.started_at ?? ""),
    )[0] ?? null;
}

function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDuration(seconds: number | null): string | null {
  if (seconds === null || seconds <= 0) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  if (m > 0) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  return `${s}s`;
}

function uniquePlateCount(scans: ScanRow[] | null | undefined): number {
  if (!scans) return 0;
  const set = new Set<string>();
  for (const s of scans) {
    if (s.plate_id) set.add(s.plate_id);
  }
  return set.size;
}

function perPlateTimepointCount(
  scans: ScanRow[] | null | undefined,
): number {
  if (!scans || scans.length === 0) return 0;
  const counts = new Map<string, number>();
  for (const s of scans) {
    if (!s.plate_id) continue;
    counts.set(s.plate_id, (counts.get(s.plate_id) ?? 0) + 1);
  }
  return counts.size === 0 ? 0 : Math.max(...counts.values());
}

function uniquePhenotyperNames(
  sessions: SessionRow[] | null | undefined,
): string[] {
  if (!sessions) return [];
  const seen = new Set<string>();
  const names: string[] = [];
  for (const s of sessions) {
    const p = s.phenotypers;
    if (!p) continue;
    const full = [p.first_name, p.last_name].filter(Boolean).join(" ").trim();
    if (full && !seen.has(full)) {
      seen.add(full);
      names.push(full);
    }
  }
  return names;
}

async function getSpeciesWithExperiments(
  speciesId: number,
): Promise<SpeciesWithGraviExperimentsFull | null> {
  const supabase = await createServerSupabaseClient();

  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("species")
    .select(
      "id, common_name, gravi_experiments!inner(id, name, system_name, cyl_scientists(scientist_name, email), accessions(name), gravi_scan_sessions(id, started_at, total_cycles, duration_seconds, actual_duration_seconds, phenotypers(first_name, last_name)), gravi_scans(plate_id))",
    )
    .eq("id", speciesId)
    .single();

  if (error) {
    console.error("[plate-phenotypes/[speciesId]] supabase error:", error);
  }

  return (data as SpeciesWithGraviExperimentsFull | null) ?? null;
}
