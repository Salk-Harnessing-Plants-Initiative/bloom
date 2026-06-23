import { redirect } from "next/navigation";
import { getUser } from "@/lib/supabase/server";
import { EmbedtreePage } from "@/components/embedtree/embedtree-page";

export const metadata = {
  title: "AI Orthologs | Bloom",
  description:
    "Find predicted orthologs across plant species using ESM-2 protein embeddings.",
};

export default async function EmbedtreeRoute() {
  const user = await getUser();
  if (!user) redirect("/login");

  return <EmbedtreePage />;
}
