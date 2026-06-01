"use client";

/**
 * Interactive trait-exploration island for the experiment page.
 *
 * Owns the trait / wave / plant-age dropdowns and the boxplot rendering.
 * Fetches per-trait data on demand from `get_scan_traits` RPC. The
 * surrounding server component (page.tsx) handles the breadcrumb + scientist
 * badge so the first paint doesn't wait on a client-side roundtrip.
 */

import { useEffect, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import ScanTraitBoxplot from "@/components/scan-trait-boxplot";
import type { TraitData } from "@/lib/custom.types";

interface TraitExplorerProps {
  experimentId: number;
  traitNames: string[];
  defaultTraitName?: string;
}

interface WaveOption {
  waveNumber: number;
  earliestDate: Date;
  latestDate: Date;
}

export default function TraitExplorer({
  experimentId,
  traitNames,
  defaultTraitName = "primary_length_mean",
}: TraitExplorerProps) {
  const [selectedTraitName, setSelectedTraitName] = useState<string>(
    traitNames.includes(defaultTraitName)
      ? defaultTraitName
      : (traitNames[0] ?? defaultTraitName),
  );
  const [traitData, setTraitData] = useState<TraitData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [waves, setWaves] = useState<WaveOption[]>([]);
  const [plantAges, setPlantAges] = useState<number[]>([]);
  const [waveNumber, setWaveNumber] = useState<number>(0);
  const [plantAge, setPlantAge] = useState<number>(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setTraitData(null);

    (async () => {
      const data = await getTraitData(experimentId, selectedTraitName);
      if (cancelled) return;

      setTraitData(data);

      const plantAgeDays = data.map((row) => row.plant_age_days);
      const plantAgeDaysUnique = [...new Set(plantAgeDays)].sort(
        (a, b) => a - b,
      );

      const waveNumbers = data.map((row) => row.wave_number);
      const wavesUnique = [...new Set(waveNumbers)].sort((a, b) => a - b);
      const waveOptions: WaveOption[] = wavesUnique.map((wave) => {
        const rows = data.filter((row) => row.wave_number === wave);
        const dates = rows.map((row) => new Date(row.date_scanned).getTime());
        return {
          waveNumber: wave,
          earliestDate: new Date(Math.min(...dates)),
          latestDate: new Date(Math.max(...dates)),
        };
      });

      setPlantAges(plantAgeDaysUnique);
      setPlantAge(plantAgeDaysUnique[plantAgeDaysUnique.length - 1] ?? 0);
      setWaves(waveOptions);
      setWaveNumber(wavesUnique[wavesUnique.length - 1] ?? 0);
      setIsLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [experimentId, selectedTraitName]);

  const filteredData = traitData?.filter(
    (row) =>
      row.plant_age_days === plantAge && row.wave_number === waveNumber,
  );

  const traitMax = (traitData ?? []).reduce(
    (max, row) => (isNaN(row.trait_value) ? max : Math.max(max, row.trait_value)),
    0,
  );

  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-end gap-4 mb-6">
        <Field label="Trait">
          <select
            className="block w-72 rounded-md border-gray-300 shadow-sm focus:border-neutral-300 focus:ring focus:ring-neutral-200 focus:ring-opacity-50"
            value={selectedTraitName}
            onChange={(e) => setSelectedTraitName(e.target.value)}
          >
            {traitNames.map((traitName) => (
              <option key={traitName} value={traitName}>
                {traitName}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Wave">
          <select
            className="block w-72 rounded-md border-gray-300 shadow-sm focus:border-neutral-300 focus:ring focus:ring-neutral-200 focus:ring-opacity-50 disabled:opacity-50"
            value={waveNumber}
            onChange={(e) => setWaveNumber(parseInt(e.target.value))}
            disabled={waves.length === 0}
          >
            {waves.map((wave) => (
              <option key={wave.waveNumber} value={wave.waveNumber}>
                {wave.waveNumber} ({wave.earliestDate.toLocaleDateString()} –{" "}
                {wave.latestDate.toLocaleDateString()})
              </option>
            ))}
          </select>
        </Field>

        <Field label="Plant age">
          <select
            className="block w-36 rounded-md border-gray-300 shadow-sm focus:border-neutral-300 focus:ring focus:ring-neutral-200 focus:ring-opacity-50 disabled:opacity-50"
            value={plantAge}
            onChange={(e) => setPlantAge(parseInt(e.target.value))}
            disabled={plantAges.length === 0}
          >
            {plantAges.map((i) => (
              <option key={i} value={i}>
                {i} days
              </option>
            ))}
          </select>
        </Field>
      </div>

      {isLoading ? (
        <LoadingState />
      ) : !filteredData || filteredData.length === 0 ? (
        <EmptyState
          message={`No "${selectedTraitName}" measurements for wave ${waveNumber} at ${plantAge} days.`}
        />
      ) : (
        <ScanTraitBoxplot traitData={filteredData} traitMax={traitMax} />
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <label className="text-xs uppercase tracking-wide text-stone-500 mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="h-72 rounded-md border border-stone-200 bg-stone-50 animate-pulse" />
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-dashed border-stone-300 bg-stone-50 p-6 text-sm text-stone-500">
      {message}
    </div>
  );
}

async function getTraitData(
  experimentId: number,
  traitName: string,
): Promise<TraitData> {
  const supabase = createClientSupabaseClient();
  const { data, error } = await supabase.rpc("get_scan_traits", {
    experiment_id_: experimentId,
    trait_name_: traitName,
  });
  if (error) console.error(error);
  return (data ?? []) as TraitData;
}
