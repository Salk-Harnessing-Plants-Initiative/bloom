"use client";
import { Suspense, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import NextLink from 'next/link';
import { Box, Stack, Typography, CircularProgress, Link as MuiLink } from '@mui/material';
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { fieldHrefs } from "@/components/plant-search-links";
import { paramsToFilters, runAdvancedSearch, filtersEmpty, AdvancedFilters } from "@/lib/cyl-plant-search";

function Results() {
  const searchParams = useSearchParams();
  const supabase = createClientSupabaseClient();

  const [rows, setRows] = useState<any[]>([]);
  const [notFound, setNotFound] = useState<string[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [empty, setEmpty] = useState(false);

  const key = searchParams.toString();

  useEffect(() => {
    const filters: AdvancedFilters = paramsToFilters(new URLSearchParams(key));
    if (filtersEmpty(filters)) {
      setRows([]); setNotFound([]); setEmpty(true); setLoading(false);
      return;
    }
    setEmpty(false);
    setLoading(true);
    let active = true;
    runAdvancedSearch(supabase, filters).then((r) => {
      if (!active) return;
      setRows(r.rows); setNotFound(r.notFound); setTruncated(r.truncated); setLoading(false);
    });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const grouped = useMemo(() => {
    const m = new Map<string, any[]>();
    for (const r of rows) {
      const k = r.species_name ?? 'Unknown';
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(r);
    }
    return Array.from(m.entries());
  }, [rows]);

  if (empty) {
    return (
      <Typography color="text.secondary">
        Use the advanced search above to search by barcodes, accessions, species, or experiment.
      </Typography>
    );
  }

  if (loading) {
    return <CircularProgress />;
  }

  return (
    <Box>
      <Typography variant="h6" sx={{ mb: 0.5 }}>Search results</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {rows.length} found{truncated ? ' (showing first 500)' : ''}
        {notFound.length ? ` · ${notFound.length} not found` : ''}
      </Typography>

      {grouped.map(([sp, group]) => (
        <Box key={sp} sx={{ mb: 2.5 }}>
          <Typography variant="overline" color="text.secondary">
            {sp} ({group.length})
          </Typography>
          <Stack spacing={0.75} sx={{ mt: 0.5 }}>
            {group.map((item) => {
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
        <Box sx={{ mt: 1 }}>
          <Typography variant="overline" color="text.secondary">Not found ({notFound.length})</Typography>
          <Typography variant="body2" color="text.secondary">{notFound.join(', ')}</Typography>
        </Box>
      )}

      {rows.length === 0 && notFound.length === 0 && (
        <Typography color="text.secondary">No matches.</Typography>
      )}
    </Box>
  );
}

export default function AdvancedSearchPage() {
  return (
    <Suspense fallback={<CircularProgress />}>
      <Results />
    </Suspense>
  );
}
