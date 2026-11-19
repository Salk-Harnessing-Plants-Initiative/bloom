'use client';

import dynamic from "next/dynamic";
import { Suspense } from "react";

import type { BrowserConfig } from "@/lib/jbrowse/config";

const JBrowse = dynamic(() => import("@/components/jbrowse"), {
  ssr: false,
  loading: () => <div className="p-4">Loading JBrowse...</div>,
});

export default function JBrowseClient({ config }: { config: BrowserConfig }) {
  return (
    <Suspense fallback={<div className="p-4">Loading JBrowse...</div>}>
      <JBrowse config={config} />
    </Suspense>
  );
}
