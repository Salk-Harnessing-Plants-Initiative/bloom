import Link from "next/link";
import { PlateImage } from "@/components/recent-phenotypes-by-plate-scanner/PlateImage";
import type { PlateGroup } from "./plateGrouping";

export function PlateList({
  plates,
  speciesId,
  experimentId,
  waveParam,
}: {
  plates: PlateGroup[];
  speciesId: string;
  experimentId: string;
  // URL wave key ("none" | "1" | …) so plate links stay scoped to this wave.
  waveParam: string;
}) {
  const plateHref = (plateId: string) =>
    `/app/plate-phenotypes/${speciesId}/${experimentId}/wave/${waveParam}/${encodeURIComponent(plateId)}`;

  return (
    <ul className="space-y-4">
      {plates.map((plate) => (
        <li
          key={plate.plate_id}
          className="rounded-lg border border-stone-200 bg-white p-4"
        >
          <div className="flex gap-5">
            <PlateImage
              path={plate.latestScan.gravi_images?.object_path ?? null}
              alt={plate.plate_id}
              className="w-[160px] h-[160px] shrink-0"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-3 flex-wrap">
                <Link
                  href={plateHref(plate.plate_id)}
                  className="text-xl text-lime-700 hover:underline"
                >
                  {plate.plate_id}
                </Link>
                {plate.accessionName && (
                  <span className="text-sm text-stone-500">
                    Accession {plate.accessionName}
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
                  href={plateHref(plate.plate_id)}
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
  );
}

export function formatDateTime(iso: string | null | undefined): string {
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
