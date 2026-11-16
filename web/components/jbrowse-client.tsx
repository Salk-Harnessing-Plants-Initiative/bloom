'use client';

import dynamic from "next/dynamic";
import { Suspense } from "react";

import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";

const JBrowse = dynamic(() => import("@/components/jbrowse"), {
  ssr: false,
  loading: () => <div className="p-4">Loading JBrowse...</div>,
});

export default function JBrowseClient({ loaded }: { loaded: LoadedBrowserConfig }) {
  return (
    <Suspense fallback={<div className="p-4">Loading JBrowse...</div>}>
      <JBrowse loaded={loaded} />
    </Suspense>
  );
}
