"use client";

import {
  // SupabaseClient,
  createClientSupabaseClient,
} from "@/lib/supabase/client";
import PlantScan from "@/components/plant-scan";
import type { SupabaseClient } from "@supabase/supabase-js";
import { Database } from "@/lib/database.types";
import { TraitData } from "@/lib/custom.types";

import { useState, useEffect } from "react";

export default function ScanTraitBoxplot({
  traitData,
  traitMax,
}: {
  traitData: TraitData;
  traitMax: number;
}) {
  //   // Create a Supabase client configured to use cookies
  // const supabase = createClientSupabaseClient();
  const supabase = createClientSupabaseClient() as unknown as SupabaseClient<Database>;

  const ySpacing = 20;
  const xOrigin = 150;
  const labelOffset = 40;
  const totalWidth = 500;
  const radius = 4;

  const [scanId, setScanId] = useState<number | null>(null);
  const [scan, setScan] = useState<any | null>(null);
  const [plantId, setPlantId] = useState<number | null>(null);
  const [imageLoading, setImageLoading] = useState<boolean>(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [offsets, setOffsets] = useState<number[]>([]);
  const [accessionY, setAccessionY] = useState<number[]>([]);
  const [accessions, setAccessions] = useState<string[]>([]);
  const [uniqueAccessions, setUniqueAccessions] = useState<string[]>([]);
  const [xScale, setXScale] = useState<number>(1);

  // useEffect(() => {
  //   console.log("re-rendering ScanTraitBoxplot");
  //   console.log("traitData = ", traitData);
  // });

  useEffect(() => {
    const get = async () => {
      const offsets = traitData
        .filter((row) => !isNaN(row.trait_value))
        .map((row) => 0);
      // .map((row) => Math.random() * 6 - 3);
      console.log("offsets = ", offsets);
      const accessions_ = traitData.map((trait) => trait.accession_name);
      const uniqueAccessions_ = [...new Set(accessions_)];
      const accessionY = uniqueAccessions_.map((accession, index) => {
        return (index + 0.5) * ySpacing;
      });
      // set scanId if plantId is set
      if (plantId) {
        const scanId_ = traitData.find(
          (row) => row.plant_id === plantId
        )?.scan_id;
        if (scanId_) setScanId(scanId_);
      }
      // calculate trait max
      const traitValues = traitData.map((trait) =>
        isNaN(trait.trait_value) ? 0 : trait.trait_value
      );
      const xScale_ = (totalWidth - xOrigin - 20) / traitMax;
      setXScale(xScale_);
      setOffsets(offsets);
      setAccessionY(accessionY);
      setAccessions(accessions_);
      setUniqueAccessions(uniqueAccessions_);
    };
    get();
  }, [traitData]);

  useEffect(() => {
    const get = async () => {
      if (scanId) {
        await getScan(scanId, supabase).then((data) => {
          setScan(data);
        });
      }
    };
    get();
  }, [scanId, supabase]);

  return (
    <div className="flex flex-row h-96">
      <div className="overflow-scroll mr-4 border-2 p-4">
        <svg height={uniqueAccessions.length * ySpacing} width={totalWidth}>
          <g>
            {uniqueAccessions.map((accession, index) => {
              const yOffset = accessionY[index];
              return (
                <g
                  key={accession}
                  transform={`translate(${xOrigin - labelOffset}, ${yOffset})`}
                >
                  <text
                    key={accession}
                    style={{
                      fontSize: "12px",
                      fontFamily: "Arial, Helvetica, sans-serif",
                      textAnchor: "end",
                      // transform: "translateX(20px)",
                    }}
                  >
                    {accession}
                  </text>
                </g>
              );
            })}
          </g>
          <g>
            {traitData
              .filter((row) => !isNaN(row.trait_value))
              .map((row, index) => {
                const accessionIndex = uniqueAccessions?.findIndex(
                  (a) => a === row.accession_name
                );
                return (
                  <circle
                    key={row.scan_id}
                    className={
                      "opacity-50 hover:fill-red-600 hover:stroke-red-600 hover:opacity-100 hover:stroke-2 cursor-pointer" +
                      (row.scan_id === scanId || row.plant_id === plantId
                        ? " fill-red-600 stroke-red-600 opacity-100 stroke-2"
                        : "")
                    }
                    onClick={() => {
                      console.log(`row.scan_id = ${row.scan_id}`);
                      setScanId(null);
                      setImageLoading(true);
                      setScanId(row.scan_id);
                      setPlantId(row.plant_id);
                    }}
                    cx={xOrigin + row.trait_value * xScale}
                    cy={accessionY[accessionIndex] - 3 + offsets[index]}
                    r={radius}
                  />
                );
              })}
          </g>
        </svg>
      </div>
      <div>
        {scanId ? (
          scan ? (
            <PlantScan
              scan={scan}
              thumb={true}
              height={250}
              href={`/app/phenotypes/${scan.cyl_plants.cyl_waves.cyl_experiments.species.id}/${scan.cyl_plants.cyl_waves.cyl_experiments.id}/${scan.cyl_plants.cyl_waves.id}/${scan.cyl_plants.accessions.id}#scan-${scan.id}`}
              label={`Plant: ${scan.cyl_plants.qr_code}`}
              target="_blank"
            />
          ) : (
            <div>Image loading...</div>
          )
        ) : null}
      </div>
    </div>
  );
}

async function getScanThumbnail(
  scanId: number,
  supabase: SupabaseClient<Database, "public", "public">
) {
  const { data } = await supabase
    .from("cyl_images")
    .select("*")
    .eq("scan_id", scanId)
    .eq("frame_number", 1)
    .single();

  return data;
}

async function getScan(
  scanId: number,
  supabase: SupabaseClient<Database, "public", "public">
) {
  const { data } = await supabase
    .from("cyl_scans")
    .select(
      "*, cyl_images(*), cyl_plants(*, accessions(*), cyl_waves(*, cyl_experiments(*, species(*))))"
    )
    .eq("id", scanId)
    .single();

  return data;
}
