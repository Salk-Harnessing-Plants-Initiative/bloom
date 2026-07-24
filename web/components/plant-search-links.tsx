"use client";
import NextLink from 'next/link';
import { Link as MuiLink } from '@mui/material';

// Per-field deep links: species -> species page, experiment -> experiment page,
// accession/barcode -> the accession-in-wave page. Null when ids are missing.
export function fieldHrefs(item: any) {
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
export function FieldLink({ href, label, value }: { href: string | null; label: string; value: any }) {
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
