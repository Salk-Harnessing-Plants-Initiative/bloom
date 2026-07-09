import { redirect } from "next/navigation";
import { getUser } from "@/lib/supabase/server";
import { OrthoVecPage } from "@/components/embedtree/orthovec-page";

export const metadata = {
  title: "OrthoVec | Bloom",
  description:
    "Protein-embedding similarity: predicted orthologs across species (ESM-2) and Arabidopsis accession comparison (ESM-3).",
};

export default async function EmbedtreeRoute() {
  const user = await getUser();
  if (!user) redirect("/login");

  return <OrthoVecPage />;
}
