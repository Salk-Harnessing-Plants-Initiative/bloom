import Link from "next/link";
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
  gravi_images: { object_path: string }[];
}

interface ExperimentRow {
  id: number;
  name: string;
  species: { id: number; common_name: string | null } | null;
}

export default async function PlateDetail({
  params,
}: {
  params: Promise<{
    speciesId: string;
    experimentId: string;
    plateId: string;
  }>;
}) {
  const { speciesId, experimentId, plateId } = await params;
  const decodedPlateId = decodeURIComponent(plateId);

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/plate-phenotypes/${speciesId}/${experimentId}/${plateId}`,
  });

  const [experiment, metadata, scans, video] = await Promise.all([
    getExperiment(Number(experimentId)),
    getPlateMetadata(Number(experimentId), decodedPlateId),
    getPlateScans(Number(experimentId), decodedPlateId),
    getPlateVideo(Number(experimentId), decodedPlateId),
  ]);

  if (!experiment) {
    return (
      <div>
        <Breadcrumb
          speciesId={speciesId}
          experimentId={experimentId}
          speciesName=""
          experimentName=""
          plateId={decodedPlateId}
        />
        <div className="text-neutral-500 italic">Experiment not found.</div>
      </div>
    );
  }

  const sections = metadata?.gravi_scan_metadata_sections ?? [];

  const timePoints: TimePoint[] = (scans ?? []).map((s) => ({
    scan_id: s.id,
    capture_date: s.capture_date,
    cycle_number: s.cycle_number,
    object_path: s.gravi_images?.[0]?.object_path ?? null,
  }));

  return (
    <div>
      <Breadcrumb
        speciesId={speciesId}
        experimentId={experimentId}
        speciesName={experiment.species?.common_name ?? ""}
        experimentName={experiment.name}
        plateId={decodedPlateId}
      />

      <div className="mb-6">
        <div className="text-3xl font-serif italic mb-1 select-none">
          {decodedPlateId}
        </div>
        <div className="text-sm text-stone-500">
          {[
            metadata?.accession_name
              ? `Accession ${metadata.accession_name}`
              : null,
            metadata?.wave_number !== null && metadata?.wave_number !== undefined
              ? `Wave ${metadata.wave_number}`
              : null,
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
          Growth time-lapse
        </h2>
        <PlateVideo objectPath={video?.object_path ?? null} />
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
        </div>
      )}

      <div className="mb-8">
        <h2 className="mb-2 text-sm uppercase tracking-widest text-stone-500">
          Time points
        </h2>
        <PlateTimeSeries points={timePoints} />
      </div>
    </div>
  );
}

function Breadcrumb({
  speciesId,
  experimentId,
  speciesName,
  experimentName,
  plateId,
}: {
  speciesId: string;
  experimentId: string;
  speciesName: string;
  experimentName: string;
  plateId: string;
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
            <span className="capitalize">{speciesName || "Species"}</span>
          </Link>
        </span>
        &nbsp;▸&nbsp;
        <span className="hover:underline">
          <Link
            href={`/app/plate-phenotypes/${speciesId}/${experimentId}`}
          >
            {experimentName
              ? capitalizeFirstLetter(experimentName.replaceAll("-", " "))
              : "Experiment"}
          </Link>
        </span>
        &nbsp;▸&nbsp;
      </span>
      <span>{plateId}</span>
    </div>
  );
}

function capitalizeFirstLetter(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
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
    console.error("[plate detail] experiment fetch error:", error);
  }
  return (data as ExperimentRow | null) ?? null;
}

async function getPlateMetadata(
  experimentId: number,
  plateId: string,
): Promise<MetadataRow | null> {
  const supabase = await createServerSupabaseClient();
  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_scan_metadata_accession")
    .select(
      "id, plate_id, accession_name, wave_number, custom_note, gravi_scan_metadata_sections(plate_section_id, medium, gravi_scan_metadata_section_plants(plant_qr))",
    )
    .eq("plate_id", plateId)
    .limit(1)
    .maybeSingle();
  if (error) {
    console.error("[plate detail] metadata fetch error:", error);
  }
  return (data as MetadataRow | null) ?? null;
}

async function getPlateVideo(
  experimentId: number,
  plateId: string,
): Promise<{ object_path: string } | null> {
  const supabase = await createServerSupabaseClient();
  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_plate_videos")
    .select("object_path, generated_at")
    .eq("experiment_id", experimentId)
    .eq("plate_id", plateId)
    .order("generated_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) {
    console.error("[plate detail] video fetch error:", error);
  }
  return (data as { object_path: string } | null) ?? null;
}

async function getPlateScans(
  experimentId: number,
  plateId: string,
): Promise<ScanRow[]> {
  const supabase = await createServerSupabaseClient();
  const { data, error } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("gravi_scans")
    .select("id, cycle_number, capture_date, gravi_images(object_path)")
    .eq("experiment_id", experimentId)
    .eq("plate_id", plateId)
    .order("capture_date", { ascending: true });
  if (error) {
    console.error("[plate detail] scans fetch error:", error);
  }
  return (data as ScanRow[] | null) ?? [];
}
