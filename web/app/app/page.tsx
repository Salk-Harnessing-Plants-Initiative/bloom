import { redirect } from "next/navigation";
import Mixpanel from "mixpanel";
import { getUser } from "@/lib/supabase/server";
import { HomeHero } from "@/components/home-hero";
import {
  fetchHomeSpeciesMontage,
  fetchSpeciesCount,
} from "@/app/app/home-lib/species-montage";

function deriveFirstName(
  user: {
    email?: string | null;
    user_metadata?: {
      name?: string | null;
      full_name?: string | null;
      first_name?: string | null;
    } | null;
  } | null,
): string | null {
  if (!user) return null;
  const meta = user.user_metadata ?? {};
  if (typeof meta.first_name === "string" && meta.first_name.trim())
    return meta.first_name.trim();
  const full =
    (typeof meta.full_name === "string" && meta.full_name) ||
    (typeof meta.name === "string" && meta.name) ||
    null;
  if (full && full.trim()) {
    const first = full.trim().split(/\s+/)[0];
    if (first) return capitalize(first);
  }
  if (typeof user.email === "string" && user.email.includes("@")) {
    const localPart = user.email.split("@")[0];
    const base = localPart.split(/[._-]/)[0];
    if (base) return capitalize(base);
  }
  return null;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export default async function HomePage() {
  const user = await getUser();
  if (!user) {
    redirect("/login");
  }

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user.email,
    url: "/app",
  });

  const [speciesCount, montage] = await Promise.all([
    fetchSpeciesCount(),
    fetchHomeSpeciesMontage(5),
  ]);

  const firstName = deriveFirstName(user);

  return (
    <div className="max-w-6xl mx-auto pb-16">
      <HomeHero
        firstName={firstName}
        speciesCount={speciesCount}
        montage={montage}
      />
    </div>
  );
}
