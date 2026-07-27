'use client';

import React from 'react';

export default function PlatePhenotypesLayout({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <main>{children}</main>
    </div>
  );
}
