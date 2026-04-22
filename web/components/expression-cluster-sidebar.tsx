"use client";

import Box from "@mui/material/Box";
import Checkbox from "@mui/material/Checkbox";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemButton from "@mui/material/ListItemButton";
import Typography from "@mui/material/Typography";
import Link from "@mui/material/Link";

import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

export interface ExpressionClusterSidebarProps {
  clusters: Cluster[];
  hiddenOrdinals: ReadonlySet<number>;
  /** Per-cluster counts (from scrna_cluster_stats). Optional; falls back to null */
  cellCounts?: Record<number, number>;
  onVisibilityChange: (ordinal: number, visible: boolean) => void;
  /**
   * Solo a cluster: hide all others so only this one is visible. Clicking
   * a cluster that is already solo restores full visibility. Parent
   * updates the hidden-ordinals set to match, so checkboxes stay in sync
   * with what is actually rendered.
   */
  onSolo: (ordinal: number) => void;
  onShowAll: () => void;
  onHideAll: () => void;
}

/**
 * Vertical list of clusters with color swatches, visibility checkboxes,
 * and click-name-to-solo behaviour. Catalog-driven — names and colors
 * come from scrna_clusters, not client-side hashing.
 */
export function ExpressionClusterSidebar({
  clusters,
  hiddenOrdinals,
  cellCounts,
  onVisibilityChange,
  onSolo,
  onShowAll,
  onHideAll,
}: ExpressionClusterSidebarProps) {
  return (
    <Box
      data-testid="expression-cluster-sidebar"
      sx={{
        width: 280,
        maxHeight: "100%",
        overflowY: "auto",
        borderRight: "1px solid",
        borderColor: "divider",
      }}
    >
      <Box
        sx={{
          px: 2,
          pt: 2,
          pb: 1,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
          Clusters ({clusters.length})
        </Typography>
        <Box sx={{ display: "flex", gap: 1 }}>
          <Link
            component="button"
            type="button"
            onClick={onShowAll}
            sx={{ fontSize: 12 }}
          >
            Show all
          </Link>
          <Link
            component="button"
            type="button"
            onClick={onHideAll}
            sx={{ fontSize: 12 }}
          >
            Hide all
          </Link>
        </Box>
      </Box>
      <List dense disablePadding>
        {clusters.map((c) => {
          const visible = !hiddenOrdinals.has(c.ordinal);
          // A cluster is "solo" when it is the only visible one.
          const soloCount = clusters.length - hiddenOrdinals.size;
          const isSolo = soloCount === 1 && visible;
          const count = cellCounts?.[c.ordinal];
          return (
            <ListItem
              key={c.ordinal}
              disablePadding
              sx={{
                bgcolor: isSolo ? "action.selected" : "transparent",
              }}
              secondaryAction={
                count != null ? (
                  <Typography
                    variant="caption"
                    sx={{ fontFamily: "monospace", pr: 1 }}
                  >
                    {count.toLocaleString()}
                  </Typography>
                ) : null
              }
            >
              <ListItemButton
                onClick={() => onSolo(c.ordinal)}
                sx={{ gap: 1, minHeight: 36 }}
                title="Click to show only this cluster (click again to restore)"
              >
                <Checkbox
                  checked={visible}
                  onClick={(e) => {
                    e.stopPropagation();
                    onVisibilityChange(c.ordinal, !visible);
                  }}
                  size="small"
                  sx={{ p: 0.5 }}
                />
                <Box
                  aria-hidden
                  sx={{
                    width: 12,
                    height: 12,
                    borderRadius: 0.5,
                    background: c.color ?? "#888",
                    flexShrink: 0,
                    border: "1px solid rgba(0,0,0,0.12)",
                  }}
                />
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    fontWeight: isSolo ? 600 : 400,
                  }}
                  title={c.name ?? c.cluster_id}
                >
                  {c.name ?? c.cluster_id}
                </Typography>
              </ListItemButton>
            </ListItem>
          );
        })}
      </List>
    </Box>
  );
}

export default ExpressionClusterSidebar;
