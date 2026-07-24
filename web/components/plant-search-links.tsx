"use client";
import NextLink from 'next/link';
import { Link as MuiLink } from '@mui/material';
import { fieldHrefs } from './searchPage.helpers';

// Re-exported so a result view can take the hrefs and the renderer from one import.
export { fieldHrefs };

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
