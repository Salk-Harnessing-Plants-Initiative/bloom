"use client";
import { useState, useEffect } from 'react';
import NextLink from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { TextField, Box, CircularProgress, List, Divider, Typography, Link as MuiLink, InputAdornment, IconButton } from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { createClientSupabaseClient } from "@/lib/supabase/client";

// Per-field deep links: species -> species page, experiment -> experiment page,
// accession/barcode -> the accession-in-wave page. Null when ids are missing.
function fieldHrefs(item: any) {
  const { species_id, experiment_id, wave_id, accession_id } = item;
  const species =
    species_id != null ? `/app/phenotypes/${species_id}` : null;
  const experiment =
    species != null && experiment_id != null
      ? `/app/phenotypes/${species_id}/${experiment_id}`
      : null;
  const accession =
    experiment != null && wave_id != null && accession_id != null
      ? `/app/phenotypes/${species_id}/${experiment_id}/${wave_id}/${accession_id}`
      : null;
  return { species, experiment, accession };
}

// One result field, linked to its page when a destination exists.
function FieldLink({ href, label, value }: { href: string | null; label: string; value: any }) {
  const body = (<><b>{label}: </b>{value}</>);
  return (
    <h1>
      {href ? (
        <MuiLink component={NextLink} href={href} underline="hover" color="inherit">
          {body}
        </MuiLink>
      ) : (
        body
      )}
    </h1>
  );
}

// A comma or newline in the input means "batch barcode list"; otherwise the
// input is a single free-text term.
function parseQuery(input: string): { list: string[] | null; text: string } {
  const raw = input.trim();
  if (/[\n,]/.test(raw)) {
    const list = raw.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);
    return { list, text: raw };
  }
  return { list: null, text: raw };
}

export default function SearchComponent() {
  const [searchQuery, setSearchQuery] = useState('');
  const [speciesResults, setSpeciesResults] = useState<any[]>([]);
  const [plantResults, setPlantResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const supabase = createClientSupabaseClient();
  const router = useRouter();
  const pathname = usePathname();

  const clearResults = () => {
    setSpeciesResults([]);
    setPlantResults([]);
  };

  // Clear the query + results on navigation (search bar persists across the layout).
  useEffect(() => {
    setSearchQuery('');
    setSpeciesResults([]);
    setPlantResults([]);
  }, [pathname]);

  // Enter / magnifier: jump straight to the species page when the text is
  // exactly one species name; otherwise just run the normal results search.
  const handleSubmit = async () => {
    const { list, text } = parseQuery(searchQuery);
    if (!list && text) {
      const { data } = await supabase
        .from('species' as any)
        .select('id')
        .ilike('common_name', text);
      if (data && data.length === 1) {
        router.push(`/app/phenotypes/${(data[0] as any).id}`);
        return;
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
    setLoading(true);
    const { list, text } = parseQuery(query);

    // Batch barcode list -> plants only.
    if (list) {
      const { data } = await supabase
        .from('cyl_plant_search' as any)
        .select('*')
        .in('qr_code', list)
        .limit(200);
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
        .limit(8),
      // Deliberately NOT matching species_name here — species matches belong in
      // the Species group, not as a flood of plant rows.
      supabase
        .from('cyl_plant_search' as any)
        .select('*')
        .or(`qr_code.ilike.%${term}%,accession_name.ilike.%${term}%`)
        .limit(100),
    ]);

    setSpeciesResults(sp.data || []);
    setPlantResults(pl.data || []);
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
        onChange={(e) => setSearchQuery(e.target.value)}
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
