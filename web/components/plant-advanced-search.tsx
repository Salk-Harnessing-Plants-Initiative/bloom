"use client";
import { useState, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Stack, TextField, Autocomplete, Button, Alert } from '@mui/material';
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { parseBarcodes, filtersToParams, filtersEmpty } from "@/lib/cyl-plant-search";

type Opt = { id: number; label: string };

export default function PlantAdvancedSearch() {
  const supabase = createClientSupabaseClient();
  const router = useRouter();

  const [barcodes, setBarcodes] = useState('');
  const [accessions, setAccessions] = useState<Opt[]>([]);
  const [species, setSpecies] = useState<Opt[]>([]);
  const [experiments, setExperiments] = useState<Opt[]>([]);

  const [accessionOpts, setAccessionOpts] = useState<Opt[]>([]);
  const [speciesOpts, setSpeciesOpts] = useState<Opt[]>([]);
  const [experimentOpts, setExperimentOpts] = useState<Opt[]>([]);

  const [optsError, setOptsError] = useState('');

  // Load the select options once (small reference tables; no new schema).
  useEffect(() => {
    let active = true;
    (async () => {
      const [sp, ex, ac] = await Promise.all([
        supabase.from('species').select('id, common_name').is('deleted_at', null).order('common_name'),
        supabase.from('cyl_experiments').select('id, name').is('deleted_at', null).order('name'),
        supabase.from('accessions').select('id, name').order('name'),
      ]);
      if (!active) return;
      // Without this an RLS denial or network failure just renders empty
      // dropdowns, indistinguishable from "there is nothing to pick".
      const failure = sp.error ?? ex.error ?? ac.error;
      if (failure) {
        console.error('Could not load advanced-search options:', failure.message);
        setOptsError('Could not load filter options — try reloading the page.');
        return;
      }
      setOptsError('');
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
  };

  const filters = useMemo(
    () => ({
      barcodes: parseBarcodes(barcodes),
      accessionIds: accessions.map((a) => a.id),
      speciesIds: species.map((s) => s.id),
      experimentIds: experiments.map((e) => e.id),
    }),
    [barcodes, accessions, species, experiments],
  );
  // Drives the Apply button's disabled state, so an empty search reads as
  // "nothing to apply" rather than a button that does nothing when pressed.
  const canApply = !filtersEmpty(filters);

  const apply = () => {
    if (!canApply) return;
    router.push(`/app/phenotypes/search?${filtersToParams(filters).toString()}`);
  };

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
        {optsError && <Alert severity="warning">{optsError}</Alert>}
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
        <Stack direction="row" spacing={1} justifyContent="flex-end">
          <Button size="small" color="inherit" onClick={clear}>Clear</Button>
          <Button
            size="small"
            variant="contained"
            disableElevation
            disabled={!canApply}
            onClick={apply}
          >
            Apply
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}
