"use client";

import type { ReactNode } from "react";
import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

export type AccordionPanel = { id: string; label: string };

/**
 * Full-width panels stacked top to bottom; at most one is open, below its header.
 * Clicking the open panel's header closes it. Only the open panel's content is rendered.
 */
export default function PanelAccordion({
  panels,
  openId,
  onOpen,
  onClose,
  children,
}: {
  panels: AccordionPanel[];
  // The open panel, or null when every panel is closed.
  openId: string | null;
  onOpen: (id: string) => void;
  onClose: (id: string) => void;
  // The open panel's content.
  children: ReactNode;
}) {
  return (
    <div>
      {panels.map((panel) => {
        const open = panel.id === openId;
        return (
          <Accordion
            key={panel.id}
            expanded={open}
            onChange={(_event, expanding) => (expanding ? onOpen(panel.id) : onClose(panel.id))}
            disableGutters
            slotProps={{ transition: { unmountOnExit: true } }}
            sx={{
              border: 1,
              borderColor: open ? "rgb(163 230 53)" : "rgb(231 229 228)",
              boxShadow: "none",
              "&:not(:last-of-type)": { mb: 1 },
              "&::before": { display: "none" },
              borderRadius: 1,
            }}
          >
            <AccordionSummary
              expandIcon={<ExpandMoreIcon />}
              id={`panel-header-${panel.id}`}
              aria-controls={`panel-${panel.id}`}
              sx={{
                bgcolor: open ? "rgb(236 252 203)" : "rgb(250 250 249)",
                color: open ? "rgb(63 98 18)" : "rgb(68 64 60)",
                fontSize: "0.875rem",
                fontWeight: 500,
                letterSpacing: "0.05em",
                textTransform: "uppercase",
                "&:hover": { bgcolor: open ? "rgb(236 252 203)" : "rgb(245 245 244)" },
              }}
            >
              {panel.label}
            </AccordionSummary>
            <AccordionDetails sx={{ p: { xs: 2, md: 3 }, overflowX: "auto" }}>
              {open ? children : null}
            </AccordionDetails>
          </Accordion>
        );
      })}
    </div>
  );
}
