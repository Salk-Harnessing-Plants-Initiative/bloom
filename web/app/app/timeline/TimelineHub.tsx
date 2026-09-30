"use client";

import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import PanelAccordion from "@/components/panel-accordion";
import { PANELS, panelHref, type PanelId } from "./panels";

/**
 * The Timeline page's panels. Opening another panel changes the URL, which loads its
 * content; closing the open one only folds it up, so reopening it needs no reload.
 */
export default function TimelineHub({ open, children }: { open: PanelId; children: ReactNode }) {
  const router = useRouter();
  // The panel the user folded up, if it is still the one in the URL.
  const [closed, setClosed] = useState<PanelId | null>(null);
  return (
    <PanelAccordion
      panels={[...PANELS]}
      openId={closed === open ? null : open}
      onOpen={(id) => {
        setClosed(null);
        if (id !== open) router.push(panelHref(id as PanelId), { scroll: false });
      }}
      onClose={(id) => setClosed(id as PanelId)}
    >
      {children}
    </PanelAccordion>
  );
}
