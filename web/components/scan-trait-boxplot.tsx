"use client";

import { createClientSupabaseClient } from "@/lib/supabase/client";
import PlantScan from "@/components/plant-scan";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Database } from "@/lib/database.types";
import { TraitData } from "@/lib/custom.types";

import { useState, useEffect, useRef } from "react";

export default function ScanTraitBoxplot({
  traitData,
  traitMax,
}: {
  traitData: TraitData;
  traitMax: number;
}) {
  const supabase = createClientSupabaseClient() as unknown as SupabaseClient<Database>;

  const ySpacing = 20;
  const xOrigin = 150;
  const labelOffset = 40;
  const radius = 4;
  const validRows = traitData.filter((row) => !isNaN(row.trait_value));
  const uniqueAccessions = [
    ...new Set(validRows.map((row) => row.accession_name)),
  ];
  const accessionY = uniqueAccessions.map(
    (_, index) => (index + 0.5) * ySpacing,
  );

  const chartContainerRef = useRef<HTMLDivElement>(null);
  const [chartWidth, setChartWidth] = useState(800);

  useEffect(() => {
    const el = chartContainerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0].contentRect.width;
      setChartWidth(Math.max(w, 500));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const xScale = traitMax > 0 ? (chartWidth - xOrigin - 20) / traitMax : 1;

  const [scanId, setScanId] = useState<number | null>(null);
  const [scan, setScan] = useState<any | null>(null);
  const [plantId, setPlantId] = useState<number | null>(null);

  const [leftWidthPct, setLeftWidthPct] = useState(65);
  const containerRef = useRef<HTMLDivElement>(null);
  const isDraggingRef = useRef(false);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDraggingRef.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      setLeftWidthPct(Math.max(25, Math.min(85, pct)));
    };
    const onUp = () => {
      if (!isDraggingRef.current) return;
      isDraggingRef.current = false;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startDrag = () => {
    isDraggingRef.current = true;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  };

  useEffect(() => {
    if (!plantId) return;
    const match = traitData.find((row) => row.plant_id === plantId);
    if (match) setScanId(match.scan_id);
  }, [traitData, plantId]);

  useEffect(() => {
    if (!scanId) return;
    let cancelled = false;
    (async () => {
      const data = await getScan(scanId, supabase);
      if (!cancelled) setScan(data);
    })();
    return () => {
      cancelled = true;
    };
  }, [scanId, supabase]);

  const perAccessionStats = uniqueAccessions.map((accession) => {
    const rows = validRows.filter((r) => r.accession_name === accession);
    const values = rows.map((r) => r.trait_value).sort((a, b) => a - b);
    const n = values.length;
    const mean = n > 0 ? values.reduce((s, v) => s + v, 0) / n : 0;
    const median =
      n === 0 ? 0 : n % 2 ? values[(n - 1) / 2] : (values[n / 2 - 1] + values[n / 2]) / 2;
    const variance =
      n > 1
        ? values.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1)
        : 0;
    const std = Math.sqrt(variance);
    const wavesRepresented = new Set(rows.map((r) => r.wave_number)).size;
    const min = values[0] ?? 0;
    const max = values[n - 1] ?? 0;
    const medianFrac =
      max > min ? (median - min) / (max - min) : 0.5;
    return {
      accession,
      n,
      mean,
      median,
      std,
      min,
      max,
      wavesRepresented,
      medianFrac,
    };
  });

  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3 flex-wrap">
        <span className="text-xs text-stone-500">
          {validRows.length} measurement{validRows.length === 1 ? "" : "s"}{" "}
          across {uniqueAccessions.length} accession
          {uniqueAccessions.length === 1 ? "" : "s"}
        </span>
        <span className="inline-flex items-center gap-2 rounded-full border border-lime-700/40 bg-lime-50 px-3 py-1 text-xs font-medium text-lime-800 shadow-sm">
          <span className="relative inline-flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-lime-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-lime-600" />
          </span>
          Click a dot to view the scan image
        </span>
      </div>

      <div
        ref={containerRef}
        className="flex flex-col gap-4 lg:flex-row lg:gap-0"
        style={
          {
            ["--left-w" as unknown as string]: `${leftWidthPct}%`,
            ["--right-w" as unknown as string]: `${100 - leftWidthPct}%`,
          } as React.CSSProperties
        }
      >
        <div
          ref={chartContainerRef}
          className="w-full lg:w-[var(--left-w)] rounded-lg border border-stone-200 bg-white p-4 shadow-sm overflow-hidden"
        >
          <svg
            height={Math.max(uniqueAccessions.length * ySpacing + 10, ySpacing * 2)}
            width={chartWidth}
            className="block"
          >
            <g>
              {uniqueAccessions.map((accession, index) => (
                <g
                  key={accession ?? `__null-${index}`}
                  transform={`translate(${xOrigin - labelOffset}, ${accessionY[index]})`}
                >
                  <text
                    style={{
                      fontSize: "12px",
                      fontFamily: "Arial, Helvetica, sans-serif",
                      textAnchor: "end",
                      fill: "rgb(68 64 60)",
                    }}
                  >
                    {accession ?? "—"}
                  </text>
                </g>
              ))}
            </g>
            <g>
              {validRows.map((row) => {
                const accessionIndex = uniqueAccessions.indexOf(
                  row.accession_name,
                );
                if (accessionIndex < 0) return null;
                const cy = accessionY[accessionIndex] - 3;
                const cx = xOrigin + row.trait_value * xScale;
                const isSelected =
                  row.scan_id === scanId || row.plant_id === plantId;
                return (
                  <circle
                    key={row.scan_id}
                    className={
                      "cursor-pointer transition-all" +
                      (isSelected
                        ? " fill-lime-600 stroke-lime-700 opacity-100 stroke-2"
                        : " fill-lime-500 stroke-lime-600 opacity-60 hover:opacity-100 hover:stroke-2")
                    }
                    onClick={() => {
                      setScanId(row.scan_id);
                      setPlantId(row.plant_id);
                    }}
                    cx={cx}
                    cy={cy}
                    r={radius}
                  />
                );
              })}
            </g>
          </svg>
        </div>

        <div
          onMouseDown={startDrag}
          className="hidden lg:flex w-3 mx-1 cursor-col-resize items-stretch shrink-0 group"
        >
          <div className="w-0.5 mx-auto h-full bg-stone-300 group-hover:bg-lime-600 transition-colors rounded-full" />
        </div>

        <div className="w-full lg:w-[var(--right-w)]">
          {scanId ? (
            scan ? (
              <PlantScan
                scan={scan}
                thumb={true}
                height={250}
                href={`/app/phenotypes/${scan.cyl_plants?.cyl_waves?.cyl_experiments?.species?.id}/${scan.cyl_plants?.cyl_waves?.cyl_experiments?.id}/${scan.cyl_plants?.cyl_waves?.id}/${scan.cyl_plants?.accessions?.id}#scan-${scan.id}`}
                label={`Plant: ${scan.cyl_plants?.qr_code}`}
                target="_blank"
              />
            ) : (
              <div className="rounded-lg border-2 border-dashed border-stone-200 bg-stone-50 px-4 py-6 text-sm text-stone-500 italic">
                Loading scan…
              </div>
            )
          ) : (
            <div className="rounded-lg border-2 border-dashed border-stone-200 bg-stone-50 px-4 py-6 text-sm text-stone-500">
              <div className="font-medium text-stone-600 mb-1">
                Scan preview
              </div>
              <div className="italic">
                Click any dot in the chart to load the corresponding plant
                scan.
              </div>
            </div>
          )}
        </div>
      </div>

      {perAccessionStats.length > 0 && (
        <div className="mt-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs uppercase tracking-wide text-stone-500">
              Per-accession summary
            </div>
            {perAccessionStats.length > 5 && (
              <div className="text-xs text-stone-400 italic">scroll ↓</div>
            )}
          </div>
          <div
            className="flex flex-col gap-2 overflow-y-scroll max-h-[26rem] rounded-lg border border-stone-200 bg-stone-50/50 p-2"
            style={{
              scrollbarWidth: "thin",
              scrollbarColor: "rgb(132 204 22 / 0.5) rgb(231 229 228)",
            }}
          >
            {perAccessionStats.map((s) => (
              <div
                key={s.accession ?? "__null"}
                className="rounded-lg border border-stone-200 border-l-4 border-l-lime-500/40 bg-white p-3 text-xs flex items-center gap-4"
              >
                <div className="w-40 flex-shrink-0">
                  <div className="font-semibold text-stone-800 truncate">
                    {s.accession ?? "—"}
                  </div>
                  <div className="mt-0.5 text-stone-500 tabular-nums">
                    n {s.n} · {s.wavesRepresented} wave
                    {s.wavesRepresented === 1 ? "" : "s"}
                  </div>
                </div>

                <div className="flex-shrink-0 w-28 flex items-baseline gap-1">
                  <span className="text-xl font-semibold text-stone-900 tabular-nums">
                    {s.median.toFixed(1)}
                  </span>
                  <span className="text-stone-500">median</span>
                </div>

                <div className="flex-shrink-0 w-36 text-stone-600 tabular-nums">
                  mean {s.mean.toFixed(1)}
                  {s.n > 1 ? ` ± ${s.std.toFixed(2)}` : ""}
                </div>

                <div className="flex-1 min-w-[140px]">
                  <div className="relative h-1.5 rounded-full bg-stone-200">
                    <div
                      className="absolute top-0 h-1.5 w-0.5 bg-lime-700"
                      style={{ left: `${s.medianFrac * 100}%` }}
                    />
                  </div>
                  <div className="mt-1 flex justify-between text-stone-400 tabular-nums">
                    <span>{s.min.toFixed(1)}</span>
                    <span>{s.max.toFixed(1)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

async function getScan(
  scanId: number,
  supabase: SupabaseClient<Database, "public", "public">,
) {
  const { data } = await supabase
    .from("cyl_scans")
    .select(
      "*, cyl_images(*), cyl_plants(*, accessions(*), cyl_waves(*, cyl_experiments(*, species(*))))",
    )
    .eq("id", scanId)
    .single();

  return data;
}
