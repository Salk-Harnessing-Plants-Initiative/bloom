"use client";
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Stack, TextField, Autocomplete, Button } from '@mui/material';
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

  // Load the select options once (small reference tables; no new schema).
  useEffect(() => {
    let active = true;
    (async () => {
      const [sp, ex, ac] = await Promise.all([
        supabase.from('species' as any).select('id, common_name').is('deleted_at', null).order('common_name'),
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
  };

  const apply = () => {
    const filters = {
      barcodes: parseBarcodes(barcodes),
      accessionIds: accessions.map((a) => a.id),
      speciesIds: species.map((s) => s.id),
      experimentIds: experiments.map((e) => e.id),
    };
    if (filtersEmpty(filters)) return;
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
          <Button size="small" variant="contained" disableElevation onClick={apply}>Apply</Button>
        </Stack>
      </Stack>
    </Box>
  );
}
