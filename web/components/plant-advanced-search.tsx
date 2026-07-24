"use client";
import { useState, useEffect, useMemo } from 'react';
import NextLink from 'next/link';
import { Box, Stack, TextField, Autocomplete, Button, Typography, CircularProgress, Link as MuiLink } from '@mui/material';
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { fieldHrefs } from './plant-search-links';

type Opt = { id: number; label: string };

const CAP = 500;
const CHUNK = 150;

const parseBarcodes = (s: string) => s.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean);

const chunk = (arr: string[], n: number): string[][] => {
  const out: string[][] = [];
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n));
  return out;
};

export default function PlantAdvancedSearch() {
  const supabase = createClientSupabaseClient();

  const [barcodes, setBarcodes] = useState('');
  const [accessions, setAccessions] = useState<Opt[]>([]);
  const [species, setSpecies] = useState<Opt[]>([]);
  const [experiments, setExperiments] = useState<Opt[]>([]);

  const [accessionOpts, setAccessionOpts] = useState<Opt[]>([]);
  const [speciesOpts, setSpeciesOpts] = useState<Opt[]>([]);
  const [experimentOpts, setExperimentOpts] = useState<Opt[]>([]);

  const [results, setResults] = useState<any[]>([]);
  const [notFound, setNotFound] = useState<string[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  // Load the select options once (small reference tables; no new schema).
  useEffect(() => {
    let active = true;
    (async () => {
      const [sp, ex, ac] = await Promise.all([
        supabase.from('species' as any).select('id, common_name').order('common_name'),
        supabase.from('cyl_experiments' as any).select('id, name').is('deleted_at', null).order('name'),
        supabase.from('accessions' as any).select('id, name').order('name'),
      ]);
      if (!active) return;
      setSpeciesOpts((sp.data || []).map((r: any) => ({ id: r.id, label: r.common_name })));
      setExperimentOpts((ex.data || []).map((r: any) => ({ id: r.id, label: r.name })));
      setAccessionOpts((ac.data || []).map((r: any) => ({ id: r.id, label: r.name })));
    })();
    return () => { active = false; };
  }, []);

  const clear = () => {
    setBarcodes('');
    setAccessions([]);
    setSpecies([]);
    setExperiments([]);
    setResults([]);
    setNotFound([]);
    setTruncated(false);
    setSearched(false);
  };

  const apply = async () => {
    const barcodeList = parseBarcodes(barcodes);
    const accessionIds = accessions.map((a) => a.id);
    const speciesIds = species.map((s) => s.id);
    const experimentIds = experiments.map((e) => e.id);

    if (!barcodeList.length && !accessionIds.length && !speciesIds.length && !experimentIds.length) {
      setResults([]); setNotFound([]); setTruncated(false); setSearched(false);
      return;
    }

    setLoading(true);
    setSearched(true);

    // AND across fields; OR within a field (.in). Exact match for list values.
    const build = (barcodeChunk?: string[]) => {
      let q: any = supabase.from('cyl_plant_search' as any).select('*');
      if (barcodeChunk) q = q.in('qr_code', barcodeChunk);
      if (accessionIds.length) q = q.in('accession_id', accessionIds);
      if (speciesIds.length) q = q.in('species_id', speciesIds);
      if (experimentIds.length) q = q.in('experiment_id', experimentIds);
      return q.limit(CAP);
    };

    let rows: any[] = [];
    if (barcodeList.length) {
      for (const c of chunk(barcodeList, CHUNK)) {
        const { data } = await build(c);
        if (data) rows.push(...data);
      }
    } else {
      const { data } = await build();
      rows = data || [];
    }

    const seen = new Set<number>();
    rows = rows.filter((r) => (seen.has(r.plant_id) ? false : (seen.add(r.plant_id), true)));

    const matched = new Set(rows.map((r) => r.qr_code));
    setNotFound(barcodeList.filter((b) => !matched.has(b)));
    setTruncated(rows.length > CAP);
    setResults(rows.slice(0, CAP));
    setLoading(false);
  };

  const grouped = useMemo(() => {
    const m = new Map<string, any[]>();
    for (const r of results) {
      const k = r.species_name ?? 'Unknown';
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(r);
    }
    return Array.from(m.entries());
  }, [results]);

  const eqOpt = (a: Opt, b: Opt) => a.id === b.id;

  return (
    <Box
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 2,
        p: 1.75,
        mt: 1,
        bgcolor: 'background.paper',
      }}
    >
      <Stack spacing={1.25}>
        <TextField
          size="small"
          label="Barcodes"
          placeholder="Paste barcodes — comma or newline separated"
          multiline
          minRows={2}
          maxRows={4}
          value={barcodes}
          onChange={(e) => setBarcodes(e.target.value)}
          fullWidth
        />
        <Autocomplete
          size="small"
          multiple
          limitTags={3}
          options={accessionOpts}
          value={accessions}
          onChange={(_, v) => setAccessions(v)}
          getOptionLabel={(o) => o.label}
          isOptionEqualToValue={eqOpt}
          renderInput={(p) => <TextField {...p} label="Accessions" />}
        />
        <Autocomplete
          size="small"
          multiple
          limitTags={3}
          options={speciesOpts}
          value={species}
          onChange={(_, v) => setSpecies(v)}
          getOptionLabel={(o) => o.label}
          isOptionEqualToValue={eqOpt}
          renderInput={(p) => <TextField {...p} label="Species" />}
        />
        <Autocomplete
          size="small"
          multiple
          limitTags={3}
          options={experimentOpts}
          value={experiments}
          onChange={(_, v) => setExperiments(v)}
          getOptionLabel={(o) => o.label}
          isOptionEqualToValue={eqOpt}
          renderInput={(p) => <TextField {...p} label="Experiment" />}
        />
        <Stack direction="row" spacing={1} alignItems="center">
          <Box sx={{ flexGrow: 1 }}>
            {loading && <CircularProgress size={18} />}
          </Box>
          <Button size="small" color="inherit" onClick={clear}>Clear</Button>
          <Button size="small" variant="contained" disableElevation onClick={apply}>Apply</Button>
        </Stack>

        {searched && !loading && (
          <Box
            sx={{
              maxHeight: 320,
              overflowY: 'auto',
              borderTop: '1px solid',
              borderColor: 'divider',
              pt: 1,
            }}
          >
            <Typography variant="caption" color="text.secondary">
              {results.length} found{truncated ? ` (showing first ${CAP})` : ''}
              {notFound.length ? ` · ${notFound.length} not found` : ''}
            </Typography>

            {grouped.map(([sp, rows]) => (
              <Box key={sp} sx={{ mt: 1.25 }}>
                <Typography variant="overline" color="text.secondary">
                  {sp} ({rows.length})
                </Typography>
                <Stack spacing={0.5} sx={{ mt: 0.25 }}>
                  {rows.map((item) => {
                    const href = fieldHrefs(item).accession;
                    const line = (
                      <>
                        <Typography component="span" variant="body2" sx={{ fontWeight: 600 }}>
                          {item.qr_code}
                        </Typography>
                        <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                          {item.experiment_name} · {item.accession_name}
                        </Typography>
                      </>
                    );
                    return (
                      <Box key={item.plant_id}>
                        {href ? (
                          <MuiLink component={NextLink} href={href} underline="hover" color="inherit">
                            {line}
                          </MuiLink>
                        ) : (
                          line
                        )}
                      </Box>
                    );
                  })}
                </Stack>
              </Box>
            ))}

            {notFound.length > 0 && (
              <Box sx={{ mt: 1.25 }}>
                <Typography variant="overline" color="text.secondary">Not found</Typography>
                <Typography variant="body2" color="text.secondary">{notFound.join(', ')}</Typography>
              </Box>
            )}

            {results.length === 0 && notFound.length === 0 && (
              <Typography variant="body2" color="text.secondary">No matches.</Typography>
            )}
          </Box>
        )}
      </Stack>
    </Box>
  );
}
