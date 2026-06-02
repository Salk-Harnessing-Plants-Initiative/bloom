'use client';

import React from 'react';
import SearchBar from '../../../components/searchPage';

export default function PlatePhenotypesLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <SearchBar />
      <main>{children}</main>
    </div>
  );
}
