"use client";

import { useState } from "react";
import Tabs from "@mui/material/Tabs";
import Tab from "@mui/material/Tab";
import Box from "@mui/material/Box";
import EmbedTreeTab from "@/components/embedtree/EmbedTreeTab";

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel({ children, value, index }: TabPanelProps) {
  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`orthofinder-tabpanel-${index}`}
      aria-labelledby={`orthofinder-tab-${index}`}
      className="flex-grow flex flex-col"
    >
      {value === index && children}
    </div>
  );
}

export default function OrthofinderPage() {
  const [tab, setTab] = useState(0);

  return (
    <div className="w-full h-screen flex flex-col">
      <div className="flex items-center justify-between px-4 py-2 bg-white rounded-lg border border-stone-200 mb-3 text-sm">
        <span className="text-neutral-600">
          Maintained by Nolan Hartwick{" "}
          <span className="font-bold text-neutral-500">(Michael Lab)</span>
        </span>
        <a
          href="mailto:nhartwick@salk.edu"
          className="text-lime-700 hover:text-lime-800 hover:underline transition-colors"
        >
          nhartwick@salk.edu
        </a>
      </div>

      <Box sx={{ borderBottom: 1, borderColor: "divider", mb: 1 }}>
        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          aria-label="OrthoBrowser tabs"
        >
          <Tab
            label="OrthoBrowser"
            id="orthofinder-tab-0"
            aria-controls="orthofinder-tabpanel-0"
          />
          <Tab
            label="Embedding Phylogenomics"
            id="orthofinder-tab-1"
            aria-controls="orthofinder-tabpanel-1"
          />
        </Tabs>
      </Box>

      <TabPanel value={tab} index={0}>
        <iframe
          src="https://resources.michael.salk.edu/misc/hpi_orthobrowser/index.html"
          className="w-full border-0 rounded-lg"
          style={{ height: "calc(100vh - 140px)" }}
          title="HPI OrthoBrowser"
          allowFullScreen
        />
      </TabPanel>

      <TabPanel value={tab} index={1}>
        <EmbedTreeTab />
      </TabPanel>
    </div>
  );
}
