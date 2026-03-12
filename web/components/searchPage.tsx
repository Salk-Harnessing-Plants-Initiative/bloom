"use client";
import { useState, useEffect } from 'react';
import { TextField, Box, CircularProgress, List, ListItem, ListItemText } from '@mui/material';
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { Database } from "@/lib/database.types";
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

    const { data, error } = await supabase
      .from('cyl_plants')
        .select(`
          *,
          wave_id (
            *,
            experiment_id (
              *,
              species_id (
                common_name
              )
            )
          ),
          accession:accession_id (
            name
          )
        `)
      .ilike('qr_code', `%${query}%`);

    console.log(data)

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
        label="Search Barcodes"
        variant="outlined"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />

      { results.length > 0 && <Box
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
      <List>
          {results.map((item,index )=> (
            <div key={item.id}>
              <h1><b>Barcode: </b>{item.qr_code}</h1>
              <h1><b>Experiment: </b>{item.wave_id?.experiment_id?.name}</h1>
              <h1><b>Species: </b>{item.wave_id?.experiment_id?.species_id?.common_name}</h1>
              <h1><b>Accession: </b>{item.accession?.name}</h1>
              {index < results.length - 1 && <Divider sx={{ my: 2 }} />}
            </div>
          ))}
      </List>
      </Box>}
    </Box>
  );
}