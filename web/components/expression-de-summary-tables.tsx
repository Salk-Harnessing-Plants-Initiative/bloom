import * as React from "react";
import { useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";

import {
  comparisonLabel,
  type countByCellType,
  type GeneTally,
  type GridCell,
  type Place,
} from "./expression-lib/de-summary";
import {
  FIRST_GROUP_COLOUR,
  SECOND_GROUP_COLOUR,
  formatFoldChange,
  groupNames,
  type DeEntry,
} from "./expression-lib/de-types";

/** Rows shown before "Show more". */
const ROWS_SHOWN = 100;
const TABLE_MAX_HEIGHT = 440;

/** Opens a comparison in the per-comparison view. */
export type OnSelect = (entry: DeEntry) => void;

const formatFdr = (value: number) => value.toExponential(2);
const formatPct = (value: number) => (Number.isNaN(value) ? "–" : `${(value * 100).toFixed(1)}%`);
const formatCells = (entry: DeEntry) =>
  `${entry.n_group1?.toLocaleString() ?? "?"} vs ${entry.n_group2?.toLocaleString() ?? "?"}`;

/** Red or blue for the group a result is higher in, or no colour. */
const colourOf = (inFirstGroup: boolean, coloured: boolean) =>
  coloured ? (inFirstGroup ? FIRST_GROUP_COLOUR : SECOND_GROUP_COLOUR) : undefined;

const placeLabel = (place: Place, showContrast: boolean) =>
  [place.entry.cluster_id, showContrast && comparisonLabel(place.entry), `higher in ${place.higherIn}`]
    .filter(Boolean)
    .join(" · ");

/** The first ROWS_SHOWN rows, and a button for the next. */
function Shown<T>({ rows, children }: { rows: T[]; children: (shown: T[]) => React.ReactNode }) {
  const [limit, setLimit] = useState(ROWS_SHOWN);
  return (
    <>
      {children(rows.slice(0, limit))}
      {rows.length > limit && (
        <Button size="small" sx={{ mt: 1 }} onClick={() => setLimit(limit + ROWS_SHOWN)}>
          Show {Math.min(ROWS_SHOWN, rows.length - limit)} more of {rows.length.toLocaleString()}
        </Button>
      )}
    </>
  );
}

/** One row per gene, with a chip for each place it passes. */
export function GenesTable({ tally, groups, showContrast, coloured, onSelect }: {
  tally: { genes: GeneTally[]; cellTypesTested: number };
  groups: { a: string; b: string } | null;
  showContrast: boolean;
  coloured: boolean;
  onSelect: OnSelect;
}) {
  return (
    <Shown rows={tally.genes}>
      {(shown) => (
        <TableContainer sx={{ maxHeight: TABLE_MAX_HEIGHT }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Gene</TableCell>
                <TableCell>Cell types</TableCell>
                {groups ? (
                  <>
                    <TableCell>{`Higher in ${groups.a}`}</TableCell>
                    <TableCell>{`Higher in ${groups.b}`}</TableCell>
                  </>
                ) : (
                  <TableCell>Results</TableCell>
                )}
                <TableCell>Lowest FDR</TableCell>
                <TableCell>Where it passes</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.map((gene) => (
                <TableRow key={gene.geneId}>
                  <TableCell>{gene.gene}</TableCell>
                  <TableCell>{`${gene.cellTypes} of ${tally.cellTypesTested}`}</TableCell>
                  {gene.higher ? (
                    gene.higher.map((h) => <TableCell key={h.group}>{h.cellTypes}</TableCell>)
                  ) : (
                    <TableCell>{gene.results}</TableCell>
                  )}
                  <TableCell>{formatFdr(gene.bestFdr)}</TableCell>
                  <TableCell>
                    <Box display="flex" flexWrap="wrap" gap={0.5}>
                      {gene.places.map((place) => {
                        const colour = colourOf(place.inFirstGroup, coloured);
                        const { a, b } = groupNames(place.entry);
                        return (
                          <Chip
                            key={place.entry.id}
                            size="small"
                            variant="outlined"
                            label={placeLabel(place, showContrast)}
                            title={`${a} ${place.entry.n_group1 ?? "?"} cells, ${b} ` +
                              `${place.entry.n_group2 ?? "?"} cells. log2FC ` +
                              `${formatFoldChange(place.result.log2fc, 2)}, FDR ${formatFdr(place.result.fdr)}`}
                            onClick={() => onSelect(place.entry)}
                            sx={{ color: colour, borderColor: colour }}
                          />
                        );
                      })}
                    </Box>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Shown>
  );
}

/** Every passing result, each opening its comparison. */
export function ResultsTable({ places, showContrast, coloured, onSelect }: {
  places: Place[];
  showContrast: boolean;
  coloured: boolean;
  onSelect: OnSelect;
}) {
  return (
    <Shown rows={places}>
      {(shown) => (
        <TableContainer sx={{ maxHeight: TABLE_MAX_HEIGHT }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Gene</TableCell>
                <TableCell>Cell type</TableCell>
                {showContrast && <TableCell>Contrast</TableCell>}
                <TableCell>Higher in</TableCell>
                <TableCell>log2FC</TableCell>
                <TableCell>FDR (within its comparison)</TableCell>
                <TableCell>Cells (first vs second group)</TableCell>
                <TableCell>% expressing (first vs second)</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.map((place) => (
                <TableRow
                  key={`${place.entry.id}-${place.result.geneId}`}
                  hover
                  tabIndex={0}
                  sx={{ cursor: "pointer" }}
                  onClick={() => onSelect(place.entry)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") onSelect(place.entry);
                  }}
                >
                  <TableCell>{place.result.gene}</TableCell>
                  <TableCell>{place.entry.cluster_id}</TableCell>
                  {showContrast && <TableCell>{comparisonLabel(place.entry)}</TableCell>}
                  <TableCell>{place.higherIn}</TableCell>
                  <TableCell>
                    <span style={{ color: colourOf(place.inFirstGroup, coloured), fontWeight: 600 }}>
                      {formatFoldChange(place.result.log2fc, 2)}
                    </span>
                  </TableCell>
                  <TableCell>{formatFdr(place.result.fdr)}</TableCell>
                  <TableCell>{formatCells(place.entry)}</TableCell>
                  <TableCell>{`${formatPct(place.result.pct1)} vs ${formatPct(place.result.pct2)}`}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Shown>
  );
}

function GridCount({ cell, name, coloured, onSelect }: {
  cell: GridCell;
  name: string;
  coloured: boolean;
  onSelect: OnSelect;
}) {
  if (!cell.tested) {
    return <Typography variant="body2" color="text.disabled">not tested</Typography>;
  }
  const [a, b] = cell.counts;
  return (
    <Box
      component="button"
      type="button"
      onClick={() => onSelect(cell.entry)}
      aria-label={`${name}: ${a.n} higher in ${a.group}, ${b.n} higher in ${b.group}`}
      sx={{
        all: "unset",
        cursor: "pointer",
        "&:hover": { textDecoration: "underline" },
        "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main" },
      }}
    >
      {a.n + b.n === 0 ? "0" : (
        <>
          <span style={{ color: colourOf(true, coloured) }}>{`${a.group} ${a.n}`}</span>
          {" · "}
          <span style={{ color: colourOf(false, coloured) }}>{`${b.group} ${b.n}`}</span>
        </>
      )}
    </Box>
  );
}

/** A cell type by contrast grid of passing counts. */
export function CellTypeGrid({ grid, coloured, onSelect }: {
  grid: ReturnType<typeof countByCellType>;
  coloured: boolean;
  onSelect: OnSelect;
}) {
  return (
    <>
      <TableContainer sx={{ maxHeight: TABLE_MAX_HEIGHT }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              <TableCell>Cell type</TableCell>
              {grid.columns.map((c) => <TableCell key={c.contrast}>{c.label}</TableCell>)}
            </TableRow>
          </TableHead>
          <TableBody>
            {grid.rows.map((row) => (
              <TableRow key={row.cellType}>
                <TableCell>{row.cellType}</TableCell>
                {row.cells.map((cell, i) => (
                  <TableCell key={grid.columns[i].contrast}>
                    {cell && (
                      <GridCount
                        cell={cell}
                        name={`${row.cellType}, ${grid.columns[i].label}`}
                        coloured={coloured}
                        onSelect={onSelect}
                      />
                    )}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
        Each cell counts the results higher in each group. Not tested: too few cells on one
        side. Empty: the cell type was not compared that way.
      </Typography>
    </>
  );
}
