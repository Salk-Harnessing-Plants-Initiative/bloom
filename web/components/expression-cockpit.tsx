"use client";

import { useState, type ReactNode } from "react";

import { ExpressionView } from "@/components/expression-view";
import DifferentialExpressionAnalysis from "@/components/expression-differential-analysis";
import { ExpressionGenesByCellType } from "@/components/expression-genes-by-cell-type";
// The remaining tabs stay disabled until their schema is plumbed through —
// re-enable one by uncommenting its import, its entry in the Tab union, its
// button and its body.
// import ExpressionCorrelation from "@/components/expression-correlation-page";
// import ExpressonDownloadFiles from "@/components/expression-download-files";
import { ExpressionThemeProvider } from "@/components/expression-theme-provider";

type Tab = "umap" | "genes" | "de"; // | "correlation" | "download";

export interface ExpressionCockpitProps {
  datasetId: number;
  datasetName: string;
}

/** Five-tab cockpit around a dataset: UMAP, gene-level, correlation, DE, downloads. */
export function ExpressionCockpit({
  datasetId,
  datasetName,
}: ExpressionCockpitProps) {
  const [tab, setTab] = useState<Tab>("umap");

  return (
    <ExpressionThemeProvider>
    <div className="bg-white border border-stone-200 rounded-lg overflow-hidden">
      {/* Tab strip */}
      <div className="border-b border-stone-200 bg-white px-5 flex items-center gap-1 overflow-x-auto">
        <TabBtn active={tab === "umap"} onClick={() => setTab("umap")}>
          UMAP
        </TabBtn>
        <TabBtn active={tab === "genes"} onClick={() => setTab("genes")}>
          Genes by cell type
        </TabBtn>
        <TabBtn active={tab === "de"} onClick={() => setTab("de")}>
          Differential expression
        </TabBtn>
        {/*
        <TabBtn
          active={tab === "correlation"}
          onClick={() => setTab("correlation")}
        >
          Correlation
        </TabBtn>
        <TabBtn active={tab === "download"} onClick={() => setTab("download")}>
          Download
        </TabBtn>
        */}
      </div>

      {/* Tab body */}
      <div className="p-5 bg-stone-50 min-h-[640px]">
        {tab === "umap" && (
          <ExpressionView datasetId={datasetId} datasetName={datasetName} />
        )}
        {tab === "genes" && <ExpressionGenesByCellType datasetId={datasetId} />}
        {tab === "de" && (
          <DifferentialExpressionAnalysis file_id={datasetId} />
        )}
        {/*
        {tab === "correlation" && <ExpressionCorrelation file_id={datasetId} />}
        {tab === "download" && (
          <ExpressonDownloadFiles
            file_id={datasetId}
            file_name={datasetName}
          />
        )}
        */}
      </div>
    </div>
    </ExpressionThemeProvider>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "relative py-3 px-4 text-sm font-medium whitespace-nowrap transition-colors " +
        (active ? "text-stone-900" : "text-stone-500 hover:text-stone-800")
      }
    >
      {children}
      {active && (
        <span className="absolute left-3 right-3 bottom-0 h-0.5 bg-lime-700 rounded-full" />
      )}
    </button>
  );
}

export default ExpressionCockpit;
