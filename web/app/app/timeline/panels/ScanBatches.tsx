import Calendar from "@/components/calendar";

export type ScanBatch = {
  date_scanned: string | null;
  species_name: string | null;
  experiment_name: string | null;
  wave_number: number | null;
  count: number | null;
};

function batchKey(row: ScanBatch, index: number) {
  return `${row.date_scanned}-${row.experiment_name}-${row.wave_number}-${index}`;
}

/** Scan batches by day (species, experiment, wave, count) and a calendar of scans per day. */
export default function ScanBatches({
  rows,
  batchesLabel,
  calendarLabel,
  empty,
}: {
  rows: ScanBatch[];
  batchesLabel: string;
  calendarLabel: string;
  empty: string;
}) {
  if (rows.length === 0) {
    return <p className="text-sm italic text-stone-500">{empty}</p>;
  }
  const days = rows.map((row) => ({
    date: new Date(row.date_scanned || ""),
    count: row.count || 0,
  }));
  return (
    <div>
      <div className="mb-3 select-none text-sm text-stone-600">{batchesLabel}</div>
      <div className="mb-8 h-48 max-w-[600px] overflow-auto rounded-md border-2 p-4">
        <table>
          <thead>
            <tr>
              <th className="pr-4 text-left text-sm">Date</th>
              <th className="pr-4 text-left text-sm">Species</th>
              <th className="pr-4 text-left text-sm">Experiment</th>
              <th className="pr-4 text-left text-sm">Wave</th>
              <th className="pr-4 text-left text-sm">Scans</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={batchKey(row, index)}>
                <td className="pr-4">{row.date_scanned}</td>
                <td className="pr-4">{row.species_name}</td>
                <td className="pr-4">{row.experiment_name}</td>
                <td className="pr-4">{row.wave_number}</td>
                <td className="pr-4">{row.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mb-3 select-none text-sm text-stone-600">{calendarLabel}</div>
      <div className="max-w-full overflow-x-auto">
        <div className="w-[1000px] rounded-md bg-white">
          <Calendar data={days} />
        </div>
      </div>
    </div>
  );
}
