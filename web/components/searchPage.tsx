"use client";
import { useState, useEffect, useRef, useMemo } from 'react';
import NextLink from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { TextField, Box, CircularProgress, List, Divider, Typography, Link as MuiLink, InputAdornment, IconButton, Button } from '@mui/material';
import { createTheme, ThemeProvider, useTheme } from '@mui/material/styles';
import { green } from '@mui/material/colors';
import SearchIcon from '@mui/icons-material/Search';
import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  fieldHrefs,
  parseQuery,
  escapeLike,
  ilikeAnyFilter,
  navigateHref,
  batchNotice,
  MAX_BATCH,
} from './searchPage.helpers';
import type { NavTarget } from './searchPage.helpers';
import { FieldLink } from './plant-search-links';
import PlantAdvancedSearch from './plant-advanced-search';

export default function SearchComponent() {
  const [searchQuery, setSearchQuery] = useState('');
  const [speciesResults, setSpeciesResults] = useState<any[]>([]);
  const [plantResults, setPlantResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  // A completed search that matched nothing. Distinct from errorMsg so "found
  // nothing" doesn't render in the same red as "the query failed".
  const [noMatches, setNoMatches] = useState(false);
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
  // The pending "still typing" search, so a submit can cancel it.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearResults = () => {
    setSpeciesResults([]);
    setPlantResults([]);
    setNoMatches(false);
  };

  // Clear the query + results on navigation (search bar persists across the layout).
  // Abort any in-flight search so a late response can't repopulate on the new page.
  useEffect(() => {
    abortRef.current?.abort();
    setSearchQuery('');
    setSpeciesResults([]);
    setPlantResults([]);
    setErrorMsg('');
    setNoMatches(false);
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

    // Enter doesn't change searchQuery, so the "still typing" timer from the last
    // keystroke is still armed. Left alone it fires mid-flight and aborts the
    // navigate lookup below, and the jump silently doesn't happen.
    if (debounceRef.current) clearTimeout(debounceRef.current);

    // Take the abort slot before the jump queries: a newer search started while
    // they run supersedes this submit, so the stale text captured in this
    // closure can't overwrite fresher results on the way out.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;

    const { list, text } = parseQuery(searchQuery);
    if (!list && text) {
      // Ask the DB where to jump: a species page, one plant's page, or nowhere.
      const { data: target, error } = await supabase
        .rpc('cyl_plant_search_navigate' as any, { p_text: text })
        .abortSignal(signal);
      if (signal.aborted) return;
      if (error) {
        console.error('Navigation lookup failed:', error.message);
        setErrorMsg('Search failed, please try again.');
        clearResults();
        return;
      }
      const dest = navigateHref(target as NavTarget | null);
      if (dest) {
        router.push(dest);
        return;
      }
    }
    if (signal.aborted) return;
    fetchResults(searchQuery);
  };

  useEffect(() => {
    debounceRef.current = setTimeout(() => {
      if (searchQuery.trim() !== '') {
        fetchResults(searchQuery);
      } else {
        clearResults();
      }
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
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
    setNoMatches(false);
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
      // Only clear the spinner if no newer search has taken the slot — that one
      // owns `loading` now. Without the guard an abort-on-navigate leaves it stuck.
      if (signal.aborted) {
        if (abortRef.current === controller) setLoading(false);
        return;
      }
      if (error) {
        console.error('Plant search failed:', error.message);
        setErrorMsg('Search failed, please try again.');
        clearResults();
        setLoading(false);
        return;
      }
      setErrorMsg(batchNotice(list.length, data?.length ?? 0));
      setSpeciesResults([]);
      setPlantResults(data || []);
      setNoMatches((data || []).length === 0);
      setLoading(false);
      return;
    }

    // Substring search: escape %/_ so a term containing them matches literally
    // instead of over-matching as a wildcard.
    const [sp, pl] = await Promise.all([
      supabase
        .from('species' as any)
        .select('id, common_name, genus, species')
        .ilike('common_name', `%${escapeLike(text)}%`)
        .is('deleted_at', null)
        .limit(8)
        .abortSignal(signal),
      // Deliberately NOT matching species_name here — species matches belong in
      // the Species group, not as a flood of plant rows.
      supabase
        .from('cyl_plant_search' as any)
        .select('*')
        .or(ilikeAnyFilter(['qr_code', 'accession_name'], text))
        .limit(100)
        .abortSignal(signal),
    ]);

    if (signal.aborted) {
      if (abortRef.current === controller) setLoading(false);
      return;
    }
    if (sp.error || pl.error) {
      console.error('Search failed:', sp.error?.message ?? pl.error?.message);
      setErrorMsg('Search failed, please try again.');
      clearResults();
      setLoading(false);
      return;
    }
    setSpeciesResults(sp.data || []);
    setPlantResults(pl.data || []);
    setNoMatches((sp.data || []).length === 0 && (pl.data || []).length === 0);
    setLoading(false);
  };

  const hasResults = speciesResults.length > 0 || plantResults.length > 0;

  return (
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

      {/* Scoped to the advanced-search subtree only — wrapping the whole
          component would recolour the search box and spinner as a side effect. */}
      <ThemeProvider theme={greenTheme}>
        <Box sx={{ mt: 1 }}>
          <Button size="small" onClick={() => setShowAdvanced((v) => !v)}>
            {showAdvanced ? 'Hide advanced search' : 'Advanced search'}
          </Button>
        </Box>
        {showAdvanced && <PlantAdvancedSearch />}
      </ThemeProvider>

      {noMatches && !hasResults && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
          No matches found.
        </Typography>
      )}

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
  );
}
