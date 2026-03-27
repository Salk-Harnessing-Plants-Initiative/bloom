"use client";

import { useState, useEffect, useRef } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { ALL_SPECIES, getSpeciesColor } from "./constants";
import TextField from "@mui/material/TextField";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";

interface GeneSearchProps {
  onSearch: (results: KnnResult[], queryUid: string) => void;
  isSearching: boolean;
  setIsSearching: (v: boolean) => void;
}

export interface KnnResult {
  uid: string;
  species: string;
  gene_id: string;
  similarity: number;
  rank: number;
  orthogroup?: string | null;
  orthogroupShared?: boolean;
}

interface GeneSuggestion {
  uid: string;
  species: string;
  gene_id: string;
}

export default function GeneSearch({
  onSearch,
  isSearching,
  setIsSearching,
}: GeneSearchProps) {
  const supabase = createClientSupabaseClient();
  const [query, setQuery] = useState("");
  const [k, setK] = useState(20);
  const [suggestions, setSuggestions] = useState<GeneSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(false);
  const [selectedSpecies, setSelectedSpecies] = useState<string[]>([
    ...ALL_SPECIES,
  ]);
  const [withinSpecies, setWithinSpecies] = useState(true);
  const [acrossSpecies, setAcrossSpecies] = useState(true);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setShowSuggestions(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if (query.length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    if (debounceRef.current) clearTimeout(debounceRef.current);

    debounceRef.current = setTimeout(async () => {
      setIsLoadingSuggestions(true);
      const { data } = await (supabase.rpc as any)("search_genes", {
        partial_id: query,
        max_results: 20,
      });
      if (data) {
        setSuggestions(data as GeneSuggestion[]);
        setShowSuggestions(true);
      }
      setIsLoadingSuggestions(false);
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  function selectSuggestion(uid: string) {
    setQuery(uid);
    setShowSuggestions(false);
  }

  function toggleSpecies(species: string) {
    setSelectedSpecies((prev) =>
      prev.includes(species)
        ? prev.filter((s) => s !== species)
        : [...prev, species]
    );
  }

  async function handleSearch() {
    if (!query.trim()) return;
    setIsSearching(true);

    const { data, error } = await (supabase.rpc as any)("knn_search", {
      query_uid: query.trim(),
      match_count: k,
    });

    if (error) {
      console.error("KNN search error:", error);
      setIsSearching(false);
      return;
    }

    // Assign global ranks (1-K) by similarity before filtering
    let results: KnnResult[] = ((data as any[]) ?? []).map((r: any, i: number) => ({
      ...r,
      rank: i + 1,
      orthogroup: null,
    }));

    // Look up orthogroup for all result genes
    const queryGeneId = query.trim().split(":").slice(1).join(":");
    const resultGeneIds = results.map((r) => r.gene_id);
    const { data: ogInfo } = await (supabase.rpc as any)("get_orthogroup_info", {
      query_gene_id: queryGeneId,
      result_gene_ids: resultGeneIds,
    });

    if (ogInfo) {
      const ogMap = new Map<string, { orthogroup: string; shared: boolean }>();
      for (const m of ogInfo as { gene_id: string; orthogroup: string; shared_with_query: boolean }[]) {
        ogMap.set(m.gene_id.toLowerCase(), { orthogroup: m.orthogroup, shared: m.shared_with_query });
      }
      results = results.map((r) => {
        const info = ogMap.get(r.gene_id.toLowerCase());
        return {
          ...r,
          orthogroup: info?.orthogroup ?? null,
          orthogroupShared: info?.shared ?? false,
        };
      });
    }

    // Filter by selected species
    results = results.filter((r) => selectedSpecies.includes(r.species));

    // Filter by within/across
    const querySpecies = query.split(":")[0];
    if (!withinSpecies) {
      results = results.filter((r) => r.species !== querySpecies);
    }
    if (!acrossSpecies) {
      results = results.filter((r) => r.species === querySpecies);
    }

    onSearch(results, query.trim());
    setIsSearching(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") {
      setShowSuggestions(false);
      handleSearch();
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-3">
        <div className="relative flex-grow" ref={dropdownRef}>
          <TextField
            label="Gene ID"
            placeholder="e.g. AT5G16970"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
            size="small"
            fullWidth
            slotProps={{
              input: {
                endAdornment: isLoadingSuggestions ? (
                  <CircularProgress size={18} />
                ) : null,
              },
            }}
          />
          {showSuggestions && suggestions.length > 0 && (
            <div className="absolute z-50 w-full mt-1 bg-white border border-stone-200 rounded-lg shadow-lg max-h-60 overflow-y-auto">
              {suggestions.map((s) => (
                <button
                  key={s.uid}
                  className="w-full text-left px-3 py-2 hover:bg-stone-50 flex items-center gap-2 text-sm"
                  onClick={() => selectSuggestion(s.uid)}
                >
                  <span
                    className="w-2 h-2 rounded-full flex-shrink-0"
                    style={{ backgroundColor: getSpeciesColor(s.species) }}
                  />
                  <span className="text-neutral-800 font-mono">{s.uid}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <TextField
          label="K"
          type="number"
          value={k}
          onChange={(e) => {
            const v = parseInt(e.target.value);
            setK(Math.min(100, Math.max(5, isNaN(v) ? 5 : v)));
          }}
          size="small"
          sx={{ width: 80 }}
          slotProps={{ htmlInput: { min: 5, max: 100 } }}
        />

        <Button
          variant="contained"
          onClick={handleSearch}
          disabled={isSearching || !query.trim()}
          sx={{ height: 40, minWidth: 100 }}
        >
          {isSearching ? <CircularProgress size={20} color="inherit" /> : "Search"}
        </Button>
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-neutral-500 mr-1">Species:</span>
          {ALL_SPECIES.map((species) => (
            <Chip
              key={species}
              label={species}
              size="small"
              variant={selectedSpecies.includes(species) ? "filled" : "outlined"}
              onClick={() => toggleSpecies(species)}
              sx={{
                borderColor: getSpeciesColor(species),
                backgroundColor: selectedSpecies.includes(species)
                  ? getSpeciesColor(species)
                  : "transparent",
                color: selectedSpecies.includes(species) ? "#fff" : getSpeciesColor(species),
                fontWeight: 500,
                textTransform: "capitalize",
                "&:hover": {
                  backgroundColor: getSpeciesColor(species),
                  color: "#fff",
                  opacity: 0.9,
                },
              }}
            />
          ))}
        </div>

        <FormControlLabel
          control={
            <Checkbox
              checked={withinSpecies}
              onChange={(e) => setWithinSpecies(e.target.checked)}
              size="small"
            />
          }
          label="Within species"
          slotProps={{ typography: { variant: "body2" } }}
        />
        <FormControlLabel
          control={
            <Checkbox
              checked={acrossSpecies}
              onChange={(e) => setAcrossSpecies(e.target.checked)}
              size="small"
            />
          }
          label="Across species"
          slotProps={{ typography: { variant: "body2" } }}
        />
      </div>
    </div>
  );
}
