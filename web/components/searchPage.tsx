"use client";
import { useState, useEffect, useRef, useMemo } from 'react';
import NextLink from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { TextField, Box, CircularProgress, List, Divider, Typography, Link as MuiLink, InputAdornment, IconButton, Button } from '@mui/material';
import { createTheme, ThemeProvider, useTheme } from '@mui/material/styles';
import { green } from '@mui/material/colors';
import SearchIcon from '@mui/icons-material/Search';
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { fieldHrefs, parseQuery, escapeLike } from './searchPage.helpers';
import { FieldLink } from './plant-search-links';
import PlantAdvancedSearch from './plant-advanced-search';

// Cap the pasted barcode batch so an oversized list can't hit PostgREST unbounded.
const MAX_BATCH = 200;

// Auto-jump reads up to this many matches; we fetch +1 to detect truncation and,
// when the set is truncated, fall back to the dropdown instead of jumping on it.
const MAX_JUMP_MATCHES = 500;

export default function SearchComponent() {
  const [searchQuery, setSearchQuery] = useState('');
  const [speciesResults, setSpeciesResults] = useState<any[]>([]);
  const [plantResults, setPlantResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const supabase = createClientSupabaseClient();
  const router = useRouter();
  const pathname = usePathname();
  const baseTheme = useTheme();
  const greenTheme = useMemo(
    () => createTheme(baseTheme, { palette: { primary: { main: green[700], light: green[500], dark: green[800] } } }),
    [baseTheme],
  );
  // Cancels the in-flight search so a slower, older response can't overwrite newer results.
  const abortRef = useRef<AbortController | null>(null);

  const clearResults = () => {
    setSpeciesResults([]);
    setPlantResults([]);
  };

  // Clear the query + results on navigation (search bar persists across the layout).
  // Abort any in-flight search so a late response can't repopulate on the new page.
  useEffect(() => {
    abortRef.current?.abort();
    setSearchQuery('');
    setSpeciesResults([]);
    setPlantResults([]);
    setErrorMsg('');
  }, [pathname]);

  // Enter / magnifier: jump straight to the species page when the text is
  // exactly one species name; otherwise just run the normal results search.
  const handleSubmit = async () => {
    // Empty submit: prompt the user instead of matching every row via ILIKE '%%'.
    if (searchQuery.trim() === '') {
      setErrorMsg('Enter a barcode, accession, or species to search.');
      clearResults();
      return;
    }
    setErrorMsg('');
    const { list, text } = parseQuery(searchQuery);
    if (!list && text) {
      // Exact species name -> species page. Escape %/_ so they match literally
      // rather than as ilike wildcards (ilike keeps case-insensitivity).
      const { data: sp } = await supabase
        .from('species' as any)
        .select('id')
        .ilike('common_name', escapeLike(text))
        .is('deleted_at', null);
      if (sp && sp.length === 1) {
        router.push(`/app/phenotypes/${(sp[0] as any).id}`);
        return;
      }
      // Exact barcode -> accession page (eq, not ilike: barcodes contain '_').
      // Barcodes are unique per wave only, so one can match across waves; only
      // auto-jump when all matches share a destination (mirrors accession below).
      const { data: pl } = await supabase
        .from('cyl_plant_search' as any)
        .select('species_id, experiment_id, wave_id, accession_id')
        .eq('qr_code', text)
        .limit(MAX_JUMP_MATCHES + 1);
      // Only auto-jump on the complete match set; if truncated, show the dropdown.
      if (pl && pl.length > 0 && pl.length <= MAX_JUMP_MATCHES) {
        const dests = Array.from(
          new Set(pl.map((r: any) => fieldHrefs(r).accession).filter(Boolean)),
        );
        if (dests.length === 1) {
          router.push(dests[0] as string);
          return;
        }
      }
      // Exact accession -> its accession page, when it maps to a single one.
      const { data: acc } = await supabase
        .from('cyl_plant_search' as any)
        .select('species_id, experiment_id, wave_id, accession_id')
        .eq('accession_name', text)
        .limit(MAX_JUMP_MATCHES + 1);
      // Only auto-jump on the complete match set; if truncated, show the dropdown.
      if (acc && acc.length > 0 && acc.length <= MAX_JUMP_MATCHES) {
        const dests = Array.from(
          new Set(acc.map((r: any) => fieldHrefs(r).accession).filter(Boolean)),
        );
        if (dests.length === 1) {
          router.push(dests[0] as string);
          return;
        }
      }
    }
    fetchResults(searchQuery);
  };

  useEffect(() => {
    const delayDebounce = setTimeout(() => {
      if (searchQuery.trim() !== '') {
        fetchResults(searchQuery);
      } else {
        clearResults();
      }
    }, 300);

    return () => clearTimeout(delayDebounce);
  }, [searchQuery]);

  // Results are grouped by what matched: a species-name match surfaces the
  // species itself (not its thousands of plants); a barcode/accession match
  // surfaces the plants, with species/experiment as context.
  const fetchResults = async (query: string) => {
    // Supersede any in-flight search: abort it, then run this one under a fresh signal.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;

    setLoading(true);
    const { list, text } = parseQuery(query);

    // Batch barcode list -> plants only. Cap the list so an oversized paste
    // can't send an unbounded .in(...) straight to PostgREST from the browser.
    if (list) {
      const capped = list.slice(0, MAX_BATCH);
      const { data, error } = await supabase
        .from('cyl_plant_search' as any)
        .select('*')
        .in('qr_code', capped)
        .limit(MAX_BATCH)
        .abortSignal(signal);
      if (signal.aborted) return;
      if (error) {
        console.error('Plant search failed:', error.message);
        setErrorMsg('Search failed, please try again.');
        clearResults();
        setLoading(false);
        return;
      }
      setErrorMsg(
        list.length > MAX_BATCH
          ? `Too many barcodes — searching the first ${MAX_BATCH}.`
          : '',
      );
      setSpeciesResults([]);
      setPlantResults(data || []);
      setLoading(false);
      return;
    }

    // Strip characters that would break the PostgREST .or() grammar.
    const term = text.replace(/[(),]/g, ' ').trim();
    const [sp, pl] = await Promise.all([
      supabase
        .from('species' as any)
        .select('id, common_name, genus, species')
        .ilike('common_name', `%${term}%`)
        .is('deleted_at', null)
        .limit(8)
        .abortSignal(signal),
      // Deliberately NOT matching species_name here — species matches belong in
      // the Species group, not as a flood of plant rows.
      supabase
        .from('cyl_plant_search' as any)
        .select('*')
        .or(`qr_code.ilike.%${term}%,accession_name.ilike.%${term}%`)
        .limit(100)
        .abortSignal(signal),
    ]);

    if (signal.aborted) return;
    if (sp.error || pl.error) {
      console.error('Search failed:', sp.error?.message ?? pl.error?.message);
      setErrorMsg('Search failed, please try again.');
      clearResults();
      setLoading(false);
      return;
    }
    setSpeciesResults(sp.data || []);
    setPlantResults(pl.data || []);
    setLoading(false);
  };

  const hasResults = speciesResults.length > 0 || plantResults.length > 0;

  return (
    <ThemeProvider theme={greenTheme}>
    <Box sx={{ width: '100%', maxWidth: 1200, mx: 'auto', mt: 4, mb: 4 }}>
      <TextField
        fullWidth
        label="Search plants — barcode, accession, or species (paste a list to batch)"
        variant="outlined"
        multiline
        maxRows={6}
        value={searchQuery}
        error={errorMsg !== ''}
        helperText={errorMsg || undefined}
        onChange={(e) => {
          setSearchQuery(e.target.value);
          if (errorMsg) setErrorMsg('');
        }}
        onKeyDown={(e) => {
          // Enter submits (jump-or-search); Shift+Enter adds a newline for lists.
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
          }
        }}
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton aria-label="search" onClick={handleSubmit} edge="end">
                <SearchIcon />
              </IconButton>
            </InputAdornment>
          ),
        }}
      />

      <Box sx={{ mt: 1 }}>
        <Button size="small" onClick={() => setShowAdvanced((v) => !v)}>
          {showAdvanced ? 'Hide advanced search' : 'Advanced search'}
        </Button>
      </Box>
      {showAdvanced && <PlantAdvancedSearch />}

      { hasResults && <Box
        sx={{
          height: 300,
          overflowY: 'auto',
          border: '1px solid #ccc',
          borderRadius: 2,
          p: 2,
          mt: 2,
        }}
      >
        {loading && <CircularProgress sx={{ mt: 2 }} />}

        {speciesResults.length > 0 && (
          <>
            <Typography variant="overline" color="text.secondary">Species</Typography>
            <List>
              {speciesResults.map((sp, index) => (
                <div key={`sp-${sp.id}`}>
                  <MuiLink component={NextLink} href={`/app/phenotypes/${sp.id}`} underline="hover" color="inherit">
                    <h1>
                      <b>Species: </b>{sp.common_name}
                      {sp.genus ? ` (${sp.genus} ${sp.species})` : ''}
                    </h1>
                  </MuiLink>
                  {index < speciesResults.length - 1 && <Divider sx={{ my: 2 }} />}
                </div>
              ))}
            </List>
          </>
        )}

        {plantResults.length > 0 && (
          <>
            <Typography variant="overline" color="text.secondary">Plants</Typography>
            <List>
              {plantResults.map((item, index) => {
                const h = fieldHrefs(item);
                return (
                  <div key={item.plant_id}>
                    <FieldLink href={h.accession} label="Barcode" value={item.qr_code} />
                    <FieldLink href={h.experiment} label="Experiment" value={item.experiment_name} />
                    <FieldLink href={h.species} label="Species" value={item.species_name} />
                    <FieldLink href={h.accession} label="Accession" value={item.accession_name} />
                    {index < plantResults.length - 1 && <Divider sx={{ my: 2 }} />}
                  </div>
                );
              })}
            </List>
          </>
        )}
      </Box>}
    </Box>
    </ThemeProvider>
  );
}
