"use client";

import { Fragment, useEffect, useState } from "react";
import Link from "next/link";

import { fetchPoint, type PointRecord } from "@/components/integration-lib/joint-client";
import { DATASET_KEY } from "@/components/integration-lib/joint-map";

export interface PointMember {
  name: string;
  role: string;
  datasetId: number;
  speciesId: number | null;
  /** "reference" for an atlas stored only as points on the map. */
  kind: string;
}

interface Props {
  embeddingId: number;
  index: number;
  member: PointMember | null;
  /** What every row says about the cell, from the labels already loaded. */
  values: { key: string; value: string | null }[];
  onClose: () => void;
}

/** One clicked cell: its dataset, barcode, and every label it carries. */
export function IntegrationPointDetails({ embeddingId, index, member, values, onClose }: Props) {
  const [point, setPoint] = useState<PointRecord | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPoint(null);
    setError(null);
    fetchPoint(embeddingId, index).then(
      (p) => {
        if (!cancelled) setPoint(p);
      },
      (err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [embeddingId, index]);

  const labels = values.filter((v) => v.key !== DATASET_KEY);

  return (
    <div
      data-testid="integration-point-details"
      className="rounded-lg border border-stone-200 bg-white p-4 text-sm"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-widest text-stone-500">Selected cell</div>
          <div className="mt-1 truncate font-medium text-stone-900" title={member?.name}>
            {member?.name ?? "Unknown dataset"}
          </div>
          {member && (
            <span
              className={`mt-1 inline-block rounded-full border px-2 py-0.5 text-[11px] ${
                member.role === "query"
                  ? "border-lime-300 bg-lime-50 text-lime-800"
                  : "border-stone-200 bg-stone-50 text-stone-600"
              }`}
            >
              {member.role}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the cell's details"
          className="rounded px-1.5 text-lg leading-none text-stone-400 hover:bg-stone-100 hover:text-stone-700"
        >
          ×
        </button>
      </div>

      <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
        <dt className="text-stone-500">Barcode</dt>
        <dd className="truncate font-mono text-stone-800" title={point?.barcode}>
          {error ? <span className="text-rose-600">{error}</span> : point?.barcode ?? "…"}
        </dd>
        {labels.map(({ key, value }) => (
          <Fragment key={key}>
            <dt className="max-w-[10rem] truncate text-stone-500" title={key}>
              {key}
            </dt>
            <dd
              className={`truncate ${value ? "text-stone-800" : "text-stone-300"}`}
              title={value ?? undefined}
            >
              {value ?? "—"}
            </dd>
          </Fragment>
        ))}
      </dl>

      {member && member.kind !== "reference" && member.speciesId !== null && (
        <Link
          href={`/app/expression/${member.speciesId}/${member.datasetId}`}
          className="mt-3 inline-block text-xs text-lime-700 hover:underline"
        >
          Open {member.name} →
        </Link>
      )}
    </div>
  );
}

export default IntegrationPointDetails;
