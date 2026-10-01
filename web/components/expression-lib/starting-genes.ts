/** The genes the Genes by cell type view opens on: the dataset's stored markers,
 *  else the genes its latest differential expression analysis finds in the most
 *  cell types. The choice itself is `startingGenes`; this reads what it needs. */

import { fetchClusterMarkers } from "@/components/expression-lib/cluster-markers";
import {
  fetchPassingResults,
  listResults,
  tallyGenes,
} from "@/components/expression-lib/de-summary";
import { DEFAULT_FDR_CUT, DEFAULT_LOG2FC_CUT } from "@/components/expression-lib/de-types";
import { startingGenes } from "@/components/expression-lib/gene-stats";
import { fetchComparisons, fetchLatestRun } from "@/components/expression-differential-analysis";
import { createClientSupabaseClient } from "@/lib/supabase/client";

/** `clusterIds` are the cell types' cluster ids in the map's order. */
export async function loadStartingGenes(
  datasetId: number,
  clusterIds: readonly string[],
): Promise<ReturnType<typeof startingGenes>> {
  const markers = await fetchClusterMarkers(datasetId);
  const byCellType = clusterIds.map((id) => markers.get(id) ?? null);
  if (byCellType.some((m) => m && m.top.length > 0)) return startingGenes(byCellType, []);

  const client = createClientSupabaseClient();
  const run = await fetchLatestRun(client, datasetId);
  if (!run) return startingGenes([], []);
  const comparisons = await fetchComparisons(client, run.id);
  const { results } = await fetchPassingResults(client, comparisons, {
    fdr: DEFAULT_FDR_CUT,
    log2fc: DEFAULT_LOG2FC_CUT,
  });
  const { genes } = tallyGenes(listResults(results, comparisons, null), comparisons, null);
  return startingGenes([], genes.map((g) => g.gene));
}
