"use client";
import { useState, useEffect } from 'react';
import { Box, Stack, TextField, Autocomplete, Button, Typography } from '@mui/material';
import { createClientSupabaseClient } from "@/lib/supabase/client";

type Opt = { id: number; label: string };

type Applied = {
  barcodes: string[];
  accessionIds: number[];
  speciesIds: number[];
  experimentIds: number[];
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

  const [applied, setApplied] = useState<Applied | null>(null);

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
    setApplied(null);
  };

  const apply = () => {
    setApplied({
      barcodes: barcodes.split(/[\s,]+/).map((t) => t.trim()).filter(Boolean),
      accessionIds: accessions.map((a) => a.id),
      speciesIds: species.map((s) => s.id),
      experimentIds: experiments.map((e) => e.id),
    });
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
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>
            {applied
              ? `${applied.barcodes.length} barcodes · ${applied.accessionIds.length} accessions · ${applied.speciesIds.length} species · ${applied.experimentIds.length} experiments`
              : ''}
          </Typography>
          <Button size="small" color="inherit" onClick={clear}>Clear</Button>
          <Button size="small" variant="contained" disableElevation onClick={apply}>Apply</Button>
        </Stack>
      </Stack>
    </Box>
  );
}
