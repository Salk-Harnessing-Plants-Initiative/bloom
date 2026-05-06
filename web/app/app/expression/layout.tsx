import type { ReactNode } from "react";

/** Shared layout for /app/expression routes — centered content column. */
export default function ExpressionLayout({
  children,
}: {
  children: ReactNode;
}) {
  return <div className="max-w-7xl mx-auto px-4">{children}</div>;
}
