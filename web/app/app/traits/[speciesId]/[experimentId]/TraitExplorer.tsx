"use client";

/**
 * Interactive trait-exploration island for the experiment page.
 *
 * Owns the trait / wave / plant-age dropdowns and the boxplot rendering.
 * Fetches per-trait data on demand from `get_scan_traits` RPC. The
 * surrounding server component (page.tsx) handles the breadcrumb + scientist
 * badge so the first paint doesn't wait on a client-side roundtrip.
 *
 * `initialWave`/`initialAge` come from the page's `?wave=&age=`, which a pipeline run's traits
 * link sets. That pair is the preferred wave and age on every load until the user picks a wave or
 * age themselves, which then becomes the preference. Each load shows the preference when the trait
 * has data there. Otherwise it shows the default (last wave, oldest age within it) with a visible
 * note (see `resolveSelection`), without forgetting the preference, so returning to a trait that
 * has the run's wave and age shows them again.
 */

import { useEffect, useRef, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import ScanTraitBoxplot from "@/components/scan-trait-boxplot";
import { TraitExportButton } from "@/components/cyl-trait-export/TraitExportButton";
import type { TraitData } from "@/lib/custom.types";
import { resolveSelection, type Selection } from "./initial-selection";

interface TraitExplorerProps {
  experimentId: number;
  traitNames: string[];
  defaultTraitName?: string;
  initialWave?: number;
  initialAge?: number;
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
  initialWave,
  initialAge,
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
  const [selectionNote, setSelectionNote] = useState<string | null>(null);

  // The wave and age each data load should show if the trait has them: the link's, until the user
  // picks one. Only the pickers write it, never a load, so a fallback or an empty trait can't
  // overwrite it. A ref, so the data effect reads the latest value without re-running on it.
  const preferred = useRef<Partial<Selection> | null>(
    initialWave === undefined && initialAge === undefined
      ? null
      : { wave: initialWave, age: initialAge },
  );

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

      const { selection, note } = resolveSelection(data, preferred.current);

      setPlantAges(plantAgeDaysUnique);
      setPlantAge(selection?.age ?? 0);
      setWaves(waveOptions);
      setWaveNumber(selection?.wave ?? 0);
      setSelectionNote(note);
      setIsLoading(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [experimentId, selectedTraitName]);

  const pick = (wave: number, age: number) => {
    setWaveNumber(wave);
    setPlantAge(age);
    preferred.current = { wave, age };
    setSelectionNote(null);
  };

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
            onChange={(e) => pick(parseInt(e.target.value), plantAge)}
            disabled={isLoading || waves.length === 0}
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
            onChange={(e) => pick(waveNumber, parseInt(e.target.value))}
            disabled={isLoading || plantAges.length === 0}
          >
            {plantAges.map((i) => (
              <option key={i} value={i}>
                {i} days
              </option>
            ))}
          </select>
        </Field>

        <TraitExportButton
          target={{ experimentId, wave: waveNumber, age: plantAge, waves: waves.map((w) => w.waveNumber), ages: plantAges }}
          disabled={isLoading}
        />
      </div>

      {/* Always mounted, so screen readers announce the note when it appears. */}
      <p
        role="status"
        aria-live="polite"
        className={
          selectionNote && !isLoading
            ? "mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"
            : "sr-only"
        }
      >
        {selectionNote && !isLoading ? selectionNote : ""}
      </p>

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
