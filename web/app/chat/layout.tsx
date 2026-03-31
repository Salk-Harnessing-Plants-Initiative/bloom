import type { ReactNode } from "react";
import { redirect } from "next/navigation";
import { getUser } from "@/lib/supabase/server";
import Link from "next/link";

export default async function ChatLayout({
  children,
}: {
  children: ReactNode;
}) {
  const user = await getUser();

  if (!user) {
    redirect("/login");
  }

  return (
    <main style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      {/* Minimal top bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 16px",
          borderBottom: "1px solid #e2e8f0",
          background: "white",
          flexShrink: 0,
        }}
      >
        <Link
          href="/app"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            textDecoration: "none",
            color: "#64748b",
            fontSize: 13,
            fontWeight: 500,
          }}
        >
          <img src="/logo.png" style={{ height: 28 }} alt="Bloom" />
          <span>&larr; Back to Bloom</span>
        </Link>
        <span style={{ fontSize: 12, color: "#94a3b8" }}>{user.email}</span>
      </div>
      <div style={{ flex: 1, overflow: "hidden" }}>{children}</div>
    </main>
  );
}
