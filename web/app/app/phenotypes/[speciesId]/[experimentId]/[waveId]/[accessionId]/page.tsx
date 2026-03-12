import Link from "next/link";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";

// import PlantImage from "@/components/plant-image";
import Mixpanel from "mixpanel";
import ScientistBadge from "@/components/scientist-badge";
import PlantScan from "@/components/plant-scan";
import { CylScanWithImages } from "@/lib/custom.types";
import { Key } from "react";

export default async function Accession({
  params,
}: {
  params: Promise<{
    experimentId: string;
    accessionId: string;
    speciesId: string;
    waveId: string;
  }>;
}) {
  const { experimentId, accessionId, speciesId, waveId } = await params;
  const experiment : any = await getExperimentWithPlants(Number(experimentId));
  const species = experiment?.species;
  const experimentName = capitalizeFirstLetter(
    experiment?.name.replaceAll("-", " ") ?? ""
  );
  const speciesName = species?.common_name ?? "";

  const plants : any = await getPlants(Number(accessionId), Number(waveId));
  const accessionName = plants?.[0]?.accessions?.name ?? "";
  const wave = plants?.[0]?.cyl_waves;
  const days = (
    plants?.map(
      (plant: { cyl_scans: any[]; }) =>
        plant.cyl_scans
          .map((scan) => scan.plant_age_days)
          .filter((day) => day !== null) as number[]
    ) ?? []
  ).flat();
  const uniqueDays = [...new Set(days as number[])].sort((a, b) => a - b);

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/phenotypes/${speciesId}/${experimentId}/${waveId}/${accessionId}`,
  });

  return (
    <div className="">
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/phenotypes">All species</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline capitalize">
            <Link href={`/app/phenotypes/${species?.id}`}>{speciesName}</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline">
            <Link href={`/app/phenotypes/${species?.id}/${experiment?.id}`}>
              {experimentName}
            </Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="select-all">
          {accessionName} (Wave {wave?.number})
        </span>
      </div>
      <div className="mb-4">
        {experiment?.people && <ScientistBadge person={experiment.people} />}
      </div>
      <div className="table-auto select-none">
        {plants?.map((plant: { id: Key | null | undefined; qr_code: any; cyl_qc_codes: any[]; cyl_scans: CylScanWithImages[]; }, index: number) => (
          <div className="table-row" key={plant.id}>
            <div className="table-cell text-lg align-middle pb-4 pr-4">
              <div className="align-middle">
                <div className="text-sm text-neutral-700 flex flex-col">
                  <div className="text-neutral-500">Replicate {index + 1}</div>
                  <div>
                    <span className="text-neutral-500">ID:</span>&nbsp;
                    <span className="text-neutral-400 select-all">
                      {plant?.qr_code ?? ""}
                    </span>
                  </div>
                  {plant?.cyl_qc_codes.length > 0 && (
                    <div>
                      <span className="text-neutral-500">QC:</span>&nbsp;
                      <span className="text-neutral-400 select-all">
                        {plant?.cyl_qc_codes.map((qc) => (
                          <span>
                            {qc.cyl_qc_sets.map((qc_set: { id: any; }) => (
                              <span
                                key={`${qc.id}-${qc_set.id}`}
                                className="rounded-md border border-red-300 bg-red-200 text-red-500 text-xs px-1 mx-1 cursor-default"
                                title={qc.cyl_qc_sets[0].name}
                              >
                                {qc.value}
                              </span>
                            ))}
                          </span>
                        ))}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </div>
            <div className="table-cell text-lg align-middle">
              <div className="text-sm mt-2 text-neutral-400 flex flex-row">
                {padMissingScans(
                  plant.cyl_scans.sort(
                    (a, b) => (a.plant_age_days ?? 0) - (b.plant_age_days ?? 0)
                  ),
                  uniqueDays
                ).map((scan, i) =>
                  "cyl_images" in scan ? (
                    <div key={i} className="mr-4">
                      {scan.cyl_images.map((image) => (
                        <div
                          key={image.id}
                          className="text-center w-56 p-2"
                          id={"scan-" + scan.id}
                        >
                          <div className="pb-1">Day {scan.plant_age_days}</div>
                          <PlantScan
                            scan={scan}
                            thumb={true}
                            height={105}
                            href={`/app/phenotypes/${species?.id}/${experiment?.id}/${waveId}/${accessionId}/${scan.id}`}
                          />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div key={i} className="mr-4">
                      <div className="text-center w-56">
                        <div className="pb-1">Day {scan.plant_age_days}</div>
                        <></>
                      </div>
                    </div>
                  )
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function padMissingScans(scans: CylScanWithImages[], uniqueDays: number[]) {
  const scansByDay = scans.reduce((acc, scan) => {
    const day = scan.plant_age_days;
    if (!acc[day!]) {
      acc[day!] = [];
    }
    acc[day!].push(scan);
    return acc;
  }, {} as Record<number, CylScanWithImages[]>);

  const scansPadded = uniqueDays.map((day) =>
    scansByDay[day] ? scansByDay[day][0] : { plant_age_days: day }
  );

  return scansPadded;
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

async function getExperimentWithPlants(experimentId: number) {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("cyl_experiments")
    .select(
      "*, cyl_waves(*, cyl_plants(*, accessions!inner(*))), species(*), people(*)"
    )
    .eq("id", experimentId)
    .single();

  return data;
}

async function getPlants(accessionId: number, waveId: number) {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("cyl_plants")
    .select(
      "*, cyl_scans(*, cyl_images(*)), cyl_waves!inner(*, cyl_experiments(*, species(*))), accessions!inner(*), cyl_qc_codes(*, cyl_qc_sets(*))"
    )
    .eq("cyl_scans.cyl_images.frame_number", 1)
    .eq("cyl_waves.id", waveId)
    .eq("accessions.id", accessionId);

  return data;
}
