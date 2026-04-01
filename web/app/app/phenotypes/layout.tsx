'use client';

import React from 'react';
import SearchBar from '../../../components/searchPage';

export default function PhenotypesLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <SearchBar />
      <main>{children}</main>
    </div>
  );
}