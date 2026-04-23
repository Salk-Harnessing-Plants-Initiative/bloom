"use client";

import { useEffect, useMemo, useState } from "react";
import Autocomplete from "@mui/material/Autocomplete";
import TextField from "@mui/material/TextField";
import CircularProgress from "@mui/material/CircularProgress";

import { searchGenes } from "@/components/expression-lib/scrna-client";

const DEBOUNCE_MS = 300;
const MAX_RESULTS = 20;

export interface ExpressionGeneSearchProps {
  datasetId: number;
  value: string | null;
  onChange: (gene: string | null) => void;
  disabled?: boolean;
}

/**
 * Debounced gene-name autocomplete backed by the scrna_gene_search RPC.
 * Prefix match on gene_name, trigram-indexed. Returns up to 20 matches.
 */
export function ExpressionGeneSearch({
  datasetId,
  value,
  onChange,
  disabled,
}: ExpressionGeneSearchProps) {
  const [input, setInput] = useState(value ?? "");
  const [options, setOptions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  // keep internal input in sync with outer-selected value
  useEffect(() => {
    setInput(value ?? "");
  }, [value]);

  useEffect(() => {
    const trimmed = input.trim();
    if (!trimmed) {
      setOptions([]);
      return;
    }
    setLoading(true);
    const id = setTimeout(async () => {
      try {
        const results = await searchGenes(datasetId, trimmed, MAX_RESULTS);
        setOptions(results);
      } catch (err) {
        console.error("[ExpressionGeneSearch] searchGenes failed:", err);
        setOptions([]);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [input, datasetId]);

  // always include the current selected value in the options list so MUI
  // doesn't treat it as an "unknown" value and strip it
  const mergedOptions = useMemo(() => {
    if (!value) return options;
    return options.includes(value) ? options : [value, ...options];
  }, [options, value]);

  return (
    <Autocomplete
      size="small"
      disabled={disabled}
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
      value={value}
      onChange={(_e, newValue) => onChange(newValue)}
      inputValue={input}
      onInputChange={(_e, newInput) => setInput(newInput)}
      options={mergedOptions}
      loading={loading}
      filterOptions={(opts) => opts}
      isOptionEqualToValue={(opt, v) => opt === v}
      renderInput={(params) => (
        <TextField
          {...params}
          label="Gene"
          placeholder="e.g. AT1G01010"
          InputProps={{
            ...params.InputProps,
            endAdornment: (
              <>
                {loading ? <CircularProgress color="inherit" size={16} /> : null}
                {params.InputProps.endAdornment}
              </>
            ),
          }}
        />
      )}
      data-testid="expression-gene-search"
    />
  );
}

export default ExpressionGeneSearch;
