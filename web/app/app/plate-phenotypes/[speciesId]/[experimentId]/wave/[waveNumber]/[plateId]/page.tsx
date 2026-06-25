import type { SupabaseClient } from "@supabase/supabase-js";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import { PlateVideo } from "@/components/recent-phenotypes-by-plate-scanner/PlateVideo";
import {
  PlateTimeSeries,
  type TimePoint,
} from "@/components/recent-phenotypes-by-plate-scanner/PlateTimeSeries";
import { parseWaveKey, waveLabel } from "../../../plateGrouping";
import { Breadcrumb, formatExperimentName } from "../../../ui";

interface PlantRow {
  plant_qr: string;
}

interface SectionRow {
  plate_section_id: string;
  medium: string | null;
  gravi_scan_metadata_section_plants: PlantRow[];
}

interface MetadataRow {
  id: number;
  plate_id: string;
  accession_name: string;
  wave_number: number | null;
  custom_note: string | null;
  gravi_scan_metadata_sections: SectionRow[];
}

interface ScanRow {
  id: number;
  cycle_number: number | null;
  capture_date: string;
  gravi_images: { object_path: string } | null;
}

interface ExperimentRow {
  id: number;
  name: string;
  species: { id: number; common_name: string | null } | null;
}

export default async function WavePlateDetail({
  params,
}: {
  params: Promise<{
    speciesId: string;
    experimentId: string;
    waveNumber: string;
    plateId: string;
  }>;
}) {
  const { speciesId, experimentId, waveNumber, plateId } = await params;
  const decodedPlateId = decodeURIComponent(plateId);
  const wave = parseWaveKey(waveNumber);

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/plate-phenotypes/${speciesId}/${experimentId}/wave/${waveNumber}/${plateId}`,
  });

  const [experiment, metadata, scans, video] = await Promise.all([
    getExperiment(Number(experimentId)),
    getPlateMetadata(decodedPlateId, wave),
    getPlateScans(Number(experimentId), decodedPlateId, wave),
    getPlateVideo(Number(experimentId), decodedPlateId),
  ]);

  const trail = [
    { label: "All species", href: "/app/plate-phenotypes" },
    {
      label: experiment?.species?.common_name || "Species",
      href: `/app/plate-phenotypes/${speciesId}`,
      capitalize: true,
    },
    {
      label: experiment ? formatExperimentName(experiment.name) : "Experiment",
      href: `/app/plate-phenotypes/${speciesId}/${experimentId}`,
    },
    {
      label: waveLabel(wave),
      href: `/app/plate-phenotypes/${speciesId}/${experimentId}/wave/${waveNumber}`,
    },
    { label: decodedPlateId },
  ];

  if (!experiment) {
    return (
      <div>
        <Breadcrumb trail={trail} />
        <div className="text-neutral-500 italic">Experiment not found.</div>
      </div>
    );
  }

  const sections = metadata?.gravi_scan_metadata_sections ?? [];

  const timePoints: TimePoint[] = (scans ?? []).map((s) => ({
    scan_id: s.id,
    capture_date: s.capture_date,
    cycle_number: s.cycle_number,
    object_path: s.gravi_images?.object_path ?? null,
  }));

  return (
    <div>
      <Breadcrumb trail={trail} />

      <div className="mb-6">
        <div className="text-3xl font-serif italic mb-1 select-none">
          {decodedPlateId}
        </div>
        <div className="text-sm text-stone-500">
          {[
            metadata?.accession_name
              ? `Accession ${metadata.accession_name}`
              : null,
            waveLabel(wave),
            metadata?.custom_note ?? null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
        <div className="mt-2 text-lg font-medium text-stone-700">
          {timePoints.length} time point{timePoints.length === 1 ? "" : "s"} ·{" "}
          {sections.length} section{sections.length === 1 ? "" : "s"}
        </div>
      </div>

      <div className="mb-8">
        <h2 className="mb-2 text-sm uppercase tracking-widest text-stone-500">
          Time points
        </h2>
        <PlateTimeSeries points={timePoints} />
      </div>

      {sections.length > 0 && (
        <div className="mb-8">
          <h2 className="mb-2 text-sm uppercase tracking-widest text-stone-500">
            Sections
          </h2>
          <div className="space-y-1">
            {sections.map((section) => (
              <div
                key={section.plate_section_id}
                className="text-sm text-stone-700"
              >
                <span className="font-medium">{section.plate_section_id}</span>
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
        </div>
      )}

      <div className="mb-8">
        <h2 className="mb-2 text-sm uppercase tracking-widest text-stone-500">
          Growth time-lapse
        </h2>
        <PlateVideo objectPath={video?.object_path ?? null} />
      </div>
    </div>
  );
}

async function getExperiment(
  experimentId: number,
): Promise<ExperimentRow | null> {
  const supabase = await createServerSupabaseClient();
  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_experiments")
    .select("id, name, species(id, common_name)")
    .eq("id", experimentId)
    .single();
  if (error) {
    console.error("[wave plate detail] experiment fetch error:", error);
  }
  return (data as ExperimentRow | null) ?? null;
}

// Metadata for one plate IN ONE WAVE. gravi_scan_metadata_accession is keyed
// (accession, plate, wave), so filtering on plate + wave returns the row for
// this wave only — not an arbitrary wave's row.
async function getPlateMetadata(
  plateId: string,
  wave: number | null,
): Promise<MetadataRow | null> {
  const supabase = await createServerSupabaseClient();
  let query = (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_scan_metadata_accession")
    .select(
      "id, plate_id, accession_name, wave_number, custom_note, gravi_scan_metadata_sections(plate_section_id, medium, gravi_scan_metadata_section_plants(plant_qr))",
    )
    .eq("plate_id", plateId);
  query = wave === null ? query.is("wave_number", null) : query.eq("wave_number", wave);

  const { data, error } = await query.limit(1).maybeSingle();
  if (error) {
    console.error("[wave plate detail] metadata fetch error:", error);
  }
  return (data as MetadataRow | null) ?? null;
}

async function getPlateVideo(
  experimentId: number,
  plateId: string,
): Promise<{ object_path: string } | null> {
  const supabase = await createServerSupabaseClient();
  // gravi_plate_videos has no wave_number column — videos are keyed by
  // (experiment, plate) only, so a plate reused across waves shares one video.
  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_plate_videos")
    .select("object_path, generated_at")
    .eq("experiment_id", experimentId)
    .eq("plate_id", plateId)
    .order("generated_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) {
    console.error("[wave plate detail] video fetch error:", error);
  }
  return (data as { object_path: string } | null) ?? null;
}

// Scans for one plate IN ONE WAVE — filtered by wave_number so timepoints are
// this wave's only (not every wave's Plate_N merged together).
async function getPlateScans(
  experimentId: number,
  plateId: string,
  wave: number | null,
): Promise<ScanRow[]> {
  const supabase = await createServerSupabaseClient();
  let query = (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_scans")
    .select("id, cycle_number, capture_date, gravi_images(object_path)")
    .eq("experiment_id", experimentId)
    .eq("plate_id", plateId);
  query = wave === null ? query.is("wave_number", null) : query.eq("wave_number", wave);

  const { data, error } = await query.order("capture_date", { ascending: true });
  if (error) {
    console.error("[wave plate detail] scans fetch error:", error);
  }
  return (data as ScanRow[] | null) ?? [];
}
