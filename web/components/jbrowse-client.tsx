'use client';

import dynamic from "next/dynamic";

const JBrowse = dynamic(() => import("@/components/jbrowse"), {
  ssr: false,
  loading: () => <div className="p-4">Loading JBrowse...</div>,
});

export default function JBrowseClient() {
  return <JBrowse />;
}
