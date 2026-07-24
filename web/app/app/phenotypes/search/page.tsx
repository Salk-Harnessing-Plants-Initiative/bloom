"use client";
import { Suspense, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import NextLink from 'next/link';
import { Box, Stack, Typography, CircularProgress, Link as MuiLink } from '@mui/material';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
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
  const [error, setError] = useState<string | null>(null);

  const key = searchParams.toString();

  useEffect(() => {
    const filters: AdvancedFilters = paramsToFilters(new URLSearchParams(key));
    if (filtersEmpty(filters)) {
      setRows([]); setNotFound([]); setEmpty(true); setError(null); setLoading(false);
      return;
    }
    setEmpty(false);
    setError(null);
    setLoading(true);
    let active = true;
    runAdvancedSearch(supabase, filters)
      .then((r) => {
        if (!active) return;
        setRows(r.rows); setNotFound(r.notFound); setTruncated(r.truncated);
      })
      .catch((e) => {
        if (!active) return;
        setError(e?.message ?? 'Search failed');
        setRows([]); setNotFound([]); setTruncated(false);
      })
      .finally(() => {
        if (active) setLoading(false);
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

  if (error) {
    return (
      <Typography color="error">
        Couldn’t run the search — {error}. Please try again.
      </Typography>
    );
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
          <Stack spacing={1} sx={{ mt: 0.75 }}>
            {group.map((item) => {
              const href = fieldHrefs(item).accession;
              const clickable = href != null;
              const bar = (
                <Box
                  sx={{
                    bgcolor: 'common.white',
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1.5,
                    px: 2,
                    py: 1.25,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 1,
                    transition: 'box-shadow 120ms ease, border-color 120ms ease',
                    ...(clickable && {
                      '&:hover': { boxShadow: 2, borderColor: 'success.main' },
                      '&:hover .bar-arrow': { opacity: 1 },
                    }),
                  }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{item.qr_code}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {item.experiment_name} · {item.accession_name}
                  </Typography>
                  {clickable && (
                    <ArrowForwardIcon
                      className="bar-arrow"
                      fontSize="small"
                      sx={{ ml: 'auto', color: 'success.main', opacity: 0, transition: 'opacity 120ms ease' }}
                    />
                  )}
                </Box>
              );
              return href ? (
                <MuiLink
                  key={item.plant_id}
                  component={NextLink}
                  href={href}
                  underline="none"
                  color="inherit"
                  sx={{ display: 'block' }}
                >
                  {bar}
                </MuiLink>
              ) : (
                <Box key={item.plant_id}>{bar}</Box>
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
