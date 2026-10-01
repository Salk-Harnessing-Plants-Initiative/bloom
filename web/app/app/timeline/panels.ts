/** The Timeline page's panels, in the order their strips appear. */
export const PANELS = [
  { id: "cylinder", label: "Cylinder scanner usage tracking" },
  { id: "plate", label: "Plate scanner usage tracking" },
  { id: "rnaseq", label: "RNA-seq runs" },
] as const;

export type PanelId = (typeof PANELS)[number]["id"];

export const DEFAULT_PANEL: PanelId = "cylinder";

/** The panel named by `?panel=`, or the default for a missing or unknown name. */
export function panelFromParam(value: string | string[] | undefined): PanelId {
  const name = Array.isArray(value) ? value[0] : value;
  return PANELS.some((p) => p.id === name) ? (name as PanelId) : DEFAULT_PANEL;
}

export function panelHref(id: PanelId): string {
  return id === DEFAULT_PANEL ? "/app/timeline" : `/app/timeline?panel=${id}`;
}
