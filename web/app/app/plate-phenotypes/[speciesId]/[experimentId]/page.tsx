import Link from "next/link";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import { PlateImage } from "@/components/recent-phenotypes-by-plate-scanner/PlateImage";

interface MetadataPlant {
  plant_qr: string;
}

interface MetadataSection {
  plate_section_id: string;
  medium: string | null;
  gravi_scan_metadata_section_plants: MetadataPlant[];
}

interface MetadataAccession {
  plate_id: string;
  accession_name: string;
  wave_number: number | null;
  gravi_scan_metadata_sections: MetadataSection[];
}

interface ScanRow {
  id: number;
  plate_id: string | null;
  plate_index: string | null;
  wave_number: number | null;
  capture_date: string;
  gravi_images: { object_path: string }[];
  gravi_scan_metadata_accession: MetadataAccession | null;
}

interface ExperimentRow {
  id: number;
  name: string;
  system_name: string | null;
  species: { id: number; common_name: string | null } | null;
  cyl_scientists: { scientist_name: string | null; email: string | null } | null;
  accessions: { name: string | null } | null;
  gravi_scans: ScanRow[];
}

interface PlateGroup {
  plate_id: string;
  latestScan: ScanRow;
  scans: ScanRow[];
  accessionName: string | null;
  waveNumber: number | null;
  sections: MetadataSection[];
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

  const plates = groupByPlate(experiment.gravi_scans ?? []);
  const totalTimepoints = experiment.gravi_scans?.length ?? 0;

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
          {plates.length} plate{plates.length === 1 ? "" : "s"} ·{" "}
          {totalTimepoints} time point{totalTimepoints === 1 ? "" : "s"} across
          all plates
        </div>
      </div>

      {plates.length === 0 ? (
        <div className="text-neutral-500 italic">
          No plate scans uploaded yet.
        </div>
      ) : (
        <ul className="space-y-4">
          {plates.map((plate) => (
            <li
              key={plate.plate_id}
              className="rounded-lg border border-stone-200 bg-white p-4"
            >
              <div className="flex gap-5">
                <PlateImage
                  path={plate.latestScan.gravi_images?.[0]?.object_path ?? null}
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
                    {plate.waveNumber !== null && (
                      <span className="text-sm text-stone-500">
                        · Wave {plate.waveNumber}
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
      )}
    </div>
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

function groupByPlate(scans: ScanRow[]): PlateGroup[] {
  const map = new Map<string, ScanRow[]>();
  for (const scan of scans) {
    if (!scan.plate_id) continue;
    const list = map.get(scan.plate_id) ?? [];
    list.push(scan);
    map.set(scan.plate_id, list);
  }

  const groups: PlateGroup[] = [];
  for (const [plate_id, list] of map) {
    list.sort((a, b) => b.capture_date.localeCompare(a.capture_date));
    const latest = list[0];
    const meta = latest.gravi_scan_metadata_accession;
    groups.push({
      plate_id,
      latestScan: latest,
      scans: list,
      accessionName: meta?.accession_name ?? null,
      waveNumber: meta?.wave_number ?? latest.wave_number ?? null,
      sections: meta?.gravi_scan_metadata_sections ?? [],
    });
  }

  groups.sort((a, b) => a.plate_id.localeCompare(b.plate_id));
  return groups;
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
