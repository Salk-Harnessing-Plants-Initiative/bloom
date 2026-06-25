import Link from "next/link";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import { PlateImage } from "@/components/recent-phenotypes-by-plate-scanner/PlateImage";
import {
  groupByWave,
  waveLabel,
  type PlateGroup,
  type ScanRow,
} from "./plateGrouping";
import { WaveTabs } from "./WaveTabs";

interface ExperimentRow {
  id: number;
  name: string;
  system_name: string | null;
  species: { id: number; common_name: string | null } | null;
  cyl_scientists: { scientist_name: string | null; email: string | null } | null;
  accessions: { name: string | null } | null;
  gravi_scans: ScanRow[];
}

export default async function PlateExperiment({
  params,
}: {
  params: Promise<{ speciesId: string; experimentId: string }>;
}) {
  const { speciesId, experimentId } = await params;
  const experiment = await getExperiment(Number(experimentId));

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/plate-phenotypes/${speciesId}/${experimentId}`,
  });

  if (!experiment) {
    return (
      <div>
        <Breadcrumb speciesId={speciesId} speciesName="" experimentName="" />
        <div className="text-neutral-500 italic">Experiment not found.</div>
      </div>
    );
  }

  const waves = groupByWave(experiment.gravi_scans ?? []);
  const allPlates = waves.flatMap((w) => w.plates);
  // Per-plate timepoint count. Plates in the same experiment typically share
  // a cadence, so showing the max is honest ("up to N"); falls back to 0 when
  // there are no scans.
  const perPlateTimepoints = allPlates.length
    ? Math.max(...allPlates.map((p) => p.scans.length))
    : 0;

  return (
    <div>
      <Breadcrumb
        speciesId={speciesId}
        speciesName={experiment.species?.common_name ?? ""}
        experimentName={experiment.name}
      />

      <div className="mb-6">
        <div className="text-3xl font-serif italic mb-1 select-none">
          {capitalizeFirstLetter(experiment.name.replaceAll("-", " "))}
        </div>
        <div className="text-sm text-stone-500">
          {[
            experiment.system_name,
            experiment.accessions?.name ?? null,
            experiment.cyl_scientists?.scientist_name
              ? `Led by ${experiment.cyl_scientists.scientist_name}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
        <div className="mt-2 text-lg font-medium text-stone-700">
          {waves.length > 1 && `${waves.length} waves · `}
          {allPlates.length} plate{allPlates.length === 1 ? "" : "s"} ·{" "}
          {perPlateTimepoints} time point
          {perPlateTimepoints === 1 ? "" : "s"}
        </div>
      </div>

      {allPlates.length === 0 ? (
        <div className="text-neutral-500 italic">
          No plate scans uploaded yet.
        </div>
      ) : waves.length === 1 ? (
        <PlateList
          plates={waves[0].plates}
          speciesId={speciesId}
          experimentId={experimentId}
        />
      ) : (
        <WaveTabs
          labels={waves.map((w) => waveLabel(w.waveNumber))}
          panels={waves.map((w) => (
            <PlateList
              key={waveLabel(w.waveNumber)}
              plates={w.plates}
              speciesId={speciesId}
              experimentId={experimentId}
            />
          ))}
        />
      )}
    </div>
  );
}

function PlateList({
  plates,
  speciesId,
  experimentId,
}: {
  plates: PlateGroup[];
  speciesId: string;
  experimentId: string;
}) {
  return (
    <ul className="space-y-4">
      {plates.map((plate) => (
        <li
          key={plate.plate_id}
          className="rounded-lg border border-stone-200 bg-white p-4"
        >
          <div className="flex gap-5">
            <PlateImage
              path={plate.latestScan.gravi_images?.object_path ?? null}
              alt={plate.plate_id}
              className="w-[160px] h-[160px] shrink-0"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-3 flex-wrap">
                <Link
                  href={`/app/plate-phenotypes/${speciesId}/${experimentId}/${encodeURIComponent(plate.plate_id)}`}
                  className="text-xl text-lime-700 hover:underline"
                >
                  {plate.plate_id}
                </Link>
                {plate.accessionName && (
                  <span className="text-sm text-stone-500">
                    Accession {plate.accessionName}
                  </span>
                )}
                <span className="text-sm text-stone-400">
                  · {plate.scans.length} time point
                  {plate.scans.length === 1 ? "" : "s"}
                </span>
              </div>

              <div className="mt-1 text-xs text-stone-500">
                Latest scan {formatDateTime(plate.latestScan.capture_date)}
              </div>

              {plate.sections.length > 0 ? (
                <div className="mt-3 space-y-1">
                  {plate.sections.map((section) => (
                    <div
                      key={section.plate_section_id}
                      className="text-sm text-stone-700"
                    >
                      <span className="font-medium">
                        {section.plate_section_id}
                      </span>
                      {section.medium && (
                        <span className="ml-2 text-xs text-stone-400">
                          ({section.medium})
                        </span>
                      )}
                      <span className="ml-3 text-stone-500">
                        {section.gravi_scan_metadata_section_plants
                          .map((p) => p.plant_qr)
                          .join(", ") || (
                          <span className="italic text-stone-400">
                            no plants registered
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-3 text-xs italic text-stone-400">
                  No section metadata registered
                </div>
              )}

              <div className="mt-3">
                <Link
                  href={`/app/plate-phenotypes/${speciesId}/${experimentId}/${encodeURIComponent(plate.plate_id)}`}
                  className="inline-flex items-center gap-1 rounded-md border border-stone-300 px-2 py-0.5 text-xs font-medium text-stone-700 hover:border-lime-700 hover:bg-lime-50 hover:text-lime-800"
                >
                  Time series
                  <span aria-hidden="true">→</span>
                </Link>
              </div>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

function Breadcrumb({
  speciesId,
  speciesName,
  experimentName,
}: {
  speciesId: string;
  speciesName: string;
  experimentName: string;
}) {
  return (
    <div className="text-xl mb-6 select-none">
      <span className="text-stone-400">
        <span className="hover:underline">
          <Link href="/app/plate-phenotypes">All species</Link>
        </span>
        &nbsp;▸&nbsp;
        <span className="hover:underline">
          <Link href={`/app/plate-phenotypes/${speciesId}`}>
            <span className="capitalize">
              {speciesName || "Species"}
            </span>
          </Link>
        </span>
        &nbsp;▸&nbsp;
      </span>
      <span>
        {experimentName
          ? capitalizeFirstLetter(experimentName.replaceAll("-", " "))
          : "Experiment"}
      </span>
    </div>
  );
}

function capitalizeFirstLetter(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

async function getExperiment(
  experimentId: number,
): Promise<ExperimentRow | null> {
  const supabase = await createServerSupabaseClient();

  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_experiments")
    .select(
      "id, name, system_name, species(id, common_name), cyl_scientists(scientist_name, email), accessions(name), gravi_scans(id, plate_id, plate_index, wave_number, capture_date, gravi_images(object_path), gravi_scan_metadata_accession(plate_id, accession_name, wave_number, gravi_scan_metadata_sections(plate_section_id, medium, gravi_scan_metadata_section_plants(plant_qr))))",
    )
    .eq("id", experimentId)
    .single();

  if (error) {
    console.error(
      "[plate-phenotypes/[speciesId]/[experimentId]] supabase error:",
      error,
    );
  }

  return (data as ExperimentRow | null) ?? null;
}
