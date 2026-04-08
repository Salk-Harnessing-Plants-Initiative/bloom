"use client";

import { useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { computeDistanceMatrix } from "./lib/distance";
import { neighborJoining } from "./lib/neighborJoining";
import { parseNewick } from "./lib/newick";
import { checkFamilyMonophyly, type MonophylyResult } from "./lib/monophyly";
import TreeVisualization from "./TreeVisualization";
import type { KnnResult } from "./GeneSearch";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";

interface GeneTreeProps {
  results: KnnResult[];
  queryUid: string;
}

export default function GeneTree({ results, queryUid }: GeneTreeProps) {
  const supabase = createClientSupabaseClient();
  const [newickStr, setNewickStr] = useState<string | null>(null);
  const [monophyly, setMonophyly] = useState<MonophylyResult[]>([]);
  const [isBuilding, setIsBuilding] = useState(false);
  const [distMatrix, setDistMatrix] = useState<number[][] | null>(null);
  const [matrixLabels, setMatrixLabels] = useState<string[]>([]);

  if (results.length < 3) return null;

  async function buildTree() {
    setIsBuilding(true);

    const uids = results.map((r) => r.uid);
    const { data, error } = await (supabase.from as any)("proteins")
      .select("uid, embedding")
      .in("uid", uids);

    if (error || !data) {
      console.error("Failed to fetch embeddings:", error);
      setIsBuilding(false);
      return;
    }

    const embeddingMap = new Map<string, number[]>();
    for (const row of data as any[]) {
      const emb = typeof row.embedding === "string"
        ? JSON.parse(row.embedding)
        : row.embedding;
      embeddingMap.set(row.uid, emb);
    }

    const labels: string[] = [];
    const vectors: number[][] = [];
    for (const r of results) {
      const vec = embeddingMap.get(r.uid);
      if (vec) { labels.push(r.uid); vectors.push(vec); }
    }

    if (labels.length < 3) { setIsBuilding(false); return; }

    const matrix = computeDistanceMatrix(vectors, "cosine");
    setDistMatrix(matrix);
    setMatrixLabels(labels);

    const nwk = neighborJoining(matrix, labels);
    setNewickStr(nwk);

    const tree = parseNewick(nwk);
    setMonophyly(checkFamilyMonophyly(tree));
    setIsBuilding(false);
  }

  function downloadNewick() {
    if (!newickStr) return;
    const blob = new Blob([newickStr], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `gene_tree_${queryUid.replace(":", "_")}.nwk`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function downloadCSV() {
    if (!distMatrix || !matrixLabels) return;
    const header = ["", ...matrixLabels].join(",");
    const rows = distMatrix.map((row, i) => [matrixLabels[i], ...row.map((v) => v.toFixed(6))].join(","));
    const csv = [header, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `distance_matrix_${queryUid.replace(":", "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-neutral-700">Gene Tree</h3>

      <div className="flex items-center gap-3">
        <Button variant="outlined" onClick={buildTree} disabled={isBuilding} sx={{ height: 40 }}>
          {isBuilding ? <CircularProgress size={20} /> : "Build Gene Tree"}
        </Button>

        {newickStr && (
          <>
            <Button variant="text" size="small" onClick={downloadNewick}>Download .nwk</Button>
            <Button variant="text" size="small" onClick={downloadCSV}>Download CSV</Button>
          </>
        )}
      </div>

      {monophyly.length > 0 && (
        <div className="space-y-1.5">
          {monophyly.map((m) => (
            <div
              key={m.family}
              className={`px-3 py-2 rounded-lg text-sm border ${
                m.status === "confirmed" ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                  : m.status === "not_confirmed" ? "bg-amber-50 border-amber-200 text-amber-800"
                    : "bg-stone-50 border-stone-200 text-stone-600"
              }`}
            >
              {m.message}
            </div>
          ))}
        </div>
      )}

      {newickStr && (
        <div className="border border-stone-200 rounded-lg bg-white p-2">
          <TreeVisualization tree={parseNewick(newickStr)} />
        </div>
      )}
    </div>
  );
}
