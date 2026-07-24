"use client";
import { useState, useEffect } from 'react';
import { TextField, Box, CircularProgress, List } from '@mui/material';
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { Divider } from '@mui/material';

export default function SearchComponent() {
  const [searchQuery, setSearchQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const supabase = createClientSupabaseClient();

  useEffect(() => {
    const delayDebounce = setTimeout(() => {
      if (searchQuery.trim() !== '') {
        fetchResults(searchQuery);
      } else {
        setResults([]);
      }
    }, 300);

    return () => clearTimeout(delayDebounce);
  }, [searchQuery]);

  const fetchResults = async (query: string) => {
    setLoading(true);

    // A comma- or newline-separated list of barcodes → resolve them all at once.
    // A single free-text term → match it against barcode, accession, or species.
    const tokens = query
      .split(/[\n,]+/)
      .map((t) => t.trim())
      .filter(Boolean);

    const base = supabase.from('cyl_plant_search').select('*');
    const term = tokens[0] ?? query.trim();
    const { data, error } =
      tokens.length > 1
        ? await base.in('qr_code', tokens)
        : await base.or(
            `qr_code.ilike.%${term}%,accession_name.ilike.%${term}%,species_name.ilike.%${term}%`
          );

    if (error) {
      console.error('Query error:', error.message);
      setResults([]);
    } else {
      setResults(data || []);
    }

    setLoading(false);
  };

  return (
    <Box sx={{ width: '100%', maxWidth: 1200, mx: 'auto', mt: 4, mb: 4 }}>
      <TextField
        fullWidth
        multiline
        minRows={1}
        label="Search by barcode, accession, or species — or paste a list of barcodes"
        variant="outlined"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />

      {loading && <CircularProgress sx={{ mt: 2 }} />}

      { results.length > 0 && <Box
        sx={{
          maxHeight: 300,
          overflowY: 'auto',
          border: '1px solid #ccc',
          borderRadius: 2,
          p: 2,
          mt: 2,
        }}
      >
      <List>
          {results.map((item, index) => (
            <div key={item.plant_id ?? index}>
              <h1><b>Barcode: </b>{item.qr_code}</h1>
              <h1><b>Experiment: </b>{item.experiment_name}</h1>
              <h1><b>Species: </b>{item.species_name}</h1>
              <h1><b>Accession: </b>{item.accession_name}</h1>
              {index < results.length - 1 && <Divider sx={{ my: 2 }} />}
            </div>
          ))}
      </List>
      </Box>}
    </Box>
  );
}
