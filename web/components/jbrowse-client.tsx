'use client';

import dynamic from "next/dynamic";
import { Suspense } from "react";

import type { JBrowseProps } from "@/components/jbrowse";

const JBrowse = dynamic(() => import("@/components/jbrowse"), {
  ssr: false,
  loading: () => <div className="p-4">Loading JBrowse...</div>,
});

export default function JBrowseClient(props: JBrowseProps) {
  return (
    <Suspense fallback={<div className="p-4">Loading JBrowse...</div>}>
      <JBrowse {...props} />
    </Suspense>
  );
}
