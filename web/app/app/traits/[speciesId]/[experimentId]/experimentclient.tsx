'use client';

import { useState, useEffect } from "react";
import Link from "next/link";
import { createBrowserSupabaseClient } from "@salk-hpi/bloom-nextjs-auth";
import ScanTraitBoxplot from "@/components/scan-trait-boxplot";
import { Database } from "@/lib/database.types";
import { CylExperimentPlus, TraitData } from "@/lib/custom.types";
import ScientistBadge from "@/components/scientist-badge";

export default function ExperimentClient({
  experimentId,
}: {
  experimentId: number;
}) {
  const defaultTraitName = "primary_length_mean";
  const [loginStatusReady, setLoginStatusReady] = useState(false);
  const [loggedIn, setLoggedIn] = useState(false);
  const [experiment, setExperiment] = useState<CylExperimentPlus | null>(null);
  const [plantAge, setPlantAge] = useState<number>(0);
  const [plantAges, setPlantAges] = useState<number[]>([]);
  const [traitData, setTraitData] = useState<TraitData | null>(null);
  const [traitNames, setTraitNames] = useState<string[]>([]);
  const [selectedTraitName, setSelectedTraitName] =
    useState<string>(defaultTraitName);
  const [waves, setWaves] = useState<
    { waveNumber: number; earliestDate: Date; latestDate: Date }[]
  >([]);
  const [waveNumber, setWaveNumber] = useState<number>(0);

  useEffect(() => {
    const get = async () => {
      const data = await getExperiment(experimentId);
      setExperiment(data as CylExperimentPlus | null);
    };
    get();
  }, [experimentId]);

  useEffect(() => {
    const get = async () => {
      const traitNames = await getTraitNames();
      setTraitNames(traitNames);
    };
    get();
  }, [experiment]);

  useEffect(() => {
    const getExperiment = async () => {
      setTraitData(null);
      const data = await getTraitData(experimentId, selectedTraitName);
      setTraitData(data);
      const plantAgeDays = (data ?? []).map((row) => row.plant_age_days);
      const plantAgeDaysUnique = [...new Set(plantAgeDays)].sort((a, b) => a - b);
      setPlantAge(Math.max(...plantAgeDaysUnique));
      setPlantAges(plantAgeDaysUnique);
      const waves_ = data?.map((row) => row.wave_number) ?? [];
      const wavesUnique = [...new Set(waves_)].sort((a, b) => a - b);
      const rowsByWave = wavesUnique.map((wave) =>
        data?.filter((row) => row.wave_number === wave)
      );
      const waveStartEndDates = rowsByWave.map((rows, i) => {
        const waveNumber = wavesUnique[i];
        const dates = rows?.map((row) => new Date(row.date_scanned).getTime()) ?? [];
        return {
          waveNumber,
          earliestDate: new Date(Math.min(...dates)),
          latestDate: new Date(Math.max(...dates)),
        };
      });
      setWaveNumber(Math.max(...wavesUnique));
      setWaves(waveStartEndDates);
    };
    getExperiment();
  }, [experimentId, selectedTraitName]);

  const experimentName = capitalizeFirstLetter(
    experiment?.name.replaceAll("-", " ") ?? ""
  );
  const speciesName = experiment?.species?.common_name ?? "";

  return (
    <div>
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/traits">All species</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline capitalize">
            <Link href={`/app/traits/${experiment?.species?.id}`}>
              {speciesName}
            </Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span>{experimentName}</span>
      </div>

      <div>{experiment?.people && <ScientistBadge person={experiment.people} />}</div>

      <div className="mt-4 flex flex-col">
        <div className="flex flex-col w-96 mb-4">
          <table>
            <tbody>
              {/* Trait selector */}
              <tr>
                <td className="text-sm pr-2">Trait</td>
                <td>
                  <select
                    className="block w-full rounded-md border-gray-300 shadow-sm"
                    value={selectedTraitName}
                    onChange={(e) => setSelectedTraitName(e.target.value)}
                  >
                    {traitNames.map((traitName) => (
                      <option key={traitName} value={traitName}>
                        {traitName}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>

              {/* Wave selector */}
              <tr>
                <td className="text-sm pr-2">Wave</td>
                <td>
                  <select
                    className="block w-full rounded-md border-gray-300 shadow-sm"
                    value={waveNumber}
                    onChange={(e) => setWaveNumber(parseInt(e.target.value))}
                  >
                    {waves.map((wave) => (
                      <option key={wave.waveNumber} value={wave.waveNumber}>
                        {wave.waveNumber} (
                        {wave.earliestDate.toLocaleDateString()} -{" "}
                        {wave.latestDate.toLocaleDateString()})
                      </option>
                    ))}
                  </select>
                </td>
              </tr>

              {/* Age selector */}
              <tr>
                <td className="text-sm pr-2">Age</td>
                <td>
                  <select
                    className="block w-full rounded-md border-gray-300 shadow-sm"
                    value={plantAge}
                    onChange={(e) => setPlantAge(parseInt(e.target.value))}
                  >
                    {plantAges.map((i) => (
                      <option key={i} value={i}>
                        {i} days
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        {traitData ? (
          <ScanTraitBoxplot
            traitData={traitData.filter(
              (row) =>
                row.plant_age_days === plantAge &&
                row.wave_number === waveNumber
            )}
            traitMax={traitData.reduce(
              (max, row) =>
                isNaN(row.trait_value) ? max : Math.max(max, row.trait_value),
              0
            )}
          />
        ) : (
          <div>Data loading...</div>
        )}
      </div>
    </div>
  );
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

async function getExperiment(experimentId: number) {
  const supabase = createBrowserSupabaseClient();

  const { data } = await supabase
    .from("cyl_experiments")
    .select(
      "*, cyl_waves(*, cyl_plants(*, accessions(*), cyl_scans(*))), species(*), people(*)"
    )
    .eq("id", experimentId)
    .single();

  return data;
}

async function getTraitNames(): Promise<string[]> {
  const supabase = createBrowserSupabaseClient();

  const { data } = await supabase
    .from("cyl_scan_trait_names")
    .select("name");

  if (!data) return [];

  return (data as { name: string | null }[]).map((row) => row.name || "");
}

async function getTraitData(
  experimentId: number,
  traitName: string
): Promise<TraitData> {
  const supabase = createBrowserSupabaseClient();

  let { data, error } = await supabase.rpc("get_scan_traits", {
    experiment_id_: experimentId,
    trait_name_: traitName,
  } as any);

  if (error) console.error(error);

  return (data ?? []) as TraitData;
}

function getPlantCount(experiment: any) {
  let plantCount = 0;
  experiment.cyl_waves.forEach((wave: any) => {
    plantCount += wave.cyl_plants.length;
  });
  return `${plantCount} plants`;
}

function getLineCount(experiment: any) {
  // empty array of strings
  let lineNames: string[] = [];
  experiment.cyl_waves.forEach((wave: any) => {
    wave.cyl_plants.forEach((plant: any) => {
      lineNames.push(plant.accession_name);
    });
  });
  const lineCount = new Set(lineNames).size;
  return `${lineCount} lines`;
}

function countStrings(strings: string[]) {
  const count: { [key: string]: number } = {};
  strings.forEach((s) => {
    count[s] = (count[s] || 0) + 1;
  });
  return count;
}

type Wave = {
  experiment_id: number | null;
  id: number;
  name: string | null;
  number: number | null;
  cyl_plants: {
    created_at: string;
    germ_day: number | null;
    germ_day_color: string | null;
    id: number;
    line_name: string | null;
    qr_code: string | null;
    wave_id: number | null;
    accessions: {}[];
  }[];
};

function getAccessions(experiment: any) {
  // empty array of strings
  let lineNames: string[] = [];
  experiment.cyl_waves.forEach((wave: Wave) => {
    wave.cyl_plants.forEach((plant: any) => {
      lineNames.push(plant.accessions.name);
    });
  });
  const plantCountObj = countStrings(lineNames);
  const plantCountArray = Object.entries(plantCountObj);
  // sort by name
  const plantCountArraySorted = plantCountArray.sort((a, b) => {
    if (a[0] < b[0]) {
      return -1;
    }
    if (a[0] > b[0]) {
      return 1;
    }
    return 0;
  });
  const plantNameCountsArray = plantCountArraySorted.map(([name, count]) => ({
    name,
    count,
  }));
  return plantNameCountsArray;
}

