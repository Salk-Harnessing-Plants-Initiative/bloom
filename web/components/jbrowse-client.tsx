'use client';

import dynamic from "next/dynamic";
import { Suspense } from "react";

import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes-server";

const JBrowse = dynamic(() => import("@/components/jbrowse"), {
  ssr: false,
  loading: () => <div className="p-4">Loading JBrowse...</div>,
});

export default function JBrowseClient({ initial }: { initial: LoadedBrowserConfig }) {
  return (
    <Suspense fallback={<div className="p-4">Loading JBrowse...</div>}>
      <JBrowse initial={initial} />
    </Suspense>
  );
}
