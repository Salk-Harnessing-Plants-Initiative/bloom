import Link from "next/link";
import Illustration from "@/components/illustration";
import type { SpeciesMontageRow } from "@/app/app/home-lib/species-montage";
import { numberToWord } from "@/app/app/home-lib/number-to-word";

interface Props {
  firstName: string | null;
  speciesCount: number;
  montage: SpeciesMontageRow[];
}

const MONTAGE_SLOTS = [
  "top-4 left-0 h-44 w-44",
  "top-0 left-36 h-36 w-36",
  "top-40 left-52 h-40 w-40",
  "top-20 left-20 h-32 w-32",
  "top-56 left-4 h-36 w-36",
];

export function HomeHero({ firstName, speciesCount, montage }: Props) {
  const word = numberToWord(speciesCount);

  return (
    <section className="relative grid grid-cols-[1.05fr_1fr] gap-8 py-12">
      <div className="flex flex-col justify-center max-w-xl">
        <div className="flex items-center gap-3 mb-4 text-xs uppercase tracking-widest text-lime-700">
          <span className="inline-block h-px w-8 bg-lime-700/50" aria-hidden />
          <span>Welcome back{firstName ? `, ${firstName}` : ""}</span>
        </div>

        <h1 className="font-serif italic text-stone-900 leading-[1.05] text-[clamp(40px,5vw,64px)] mb-5">
          <span className="text-lime-700">{word}</span> plants,{" "}
          <span>studied so far.</span>
        </h1>

        <p className="text-base leading-relaxed text-stone-600 mb-8">
          Bloom is the data platform for the Salk Harnessing Plants Initiative
          — phenotyping scans, single-cell expression atlases, gene
          candidates, and eight years of field work, all in one place.
        </p>

        <div className="flex flex-wrap gap-3">
          <Link
            href="/app/phenotypes"
            className="inline-flex items-center gap-2 rounded-md bg-lime-700 px-4 py-2.5 text-sm font-medium text-stone-50 hover:bg-lime-800 transition-colors"
          >
            Explore phenotypes
            <span aria-hidden>→</span>
          </Link>
          <Link
            href="/chat"
            className="inline-flex items-center rounded-md border border-stone-300 px-4 py-2.5 text-sm font-medium text-stone-700 hover:bg-stone-50 hover:border-stone-400 transition-colors"
          >
            Ask the Bloom Assistant
          </Link>
        </div>
      </div>

      <div
        className="relative h-[360px] w-full pointer-events-none"
        aria-hidden
      >
        {montage.slice(0, 5).map((sp, i) => (
          <div
            key={sp.id}
            className={`absolute ${MONTAGE_SLOTS[i % MONTAGE_SLOTS.length]} pointer-events-auto`}
            style={{
              animation: `home-plant-drift ${14 + i * 2}s ease-in-out infinite`,
              animationDelay: `${i * -2}s`,
              filter: "saturate(0.85) contrast(0.95)",
              opacity: 0.9,
            }}
            title={sp.common_name ?? ""}
          >
            <Illustration
              path={sp.illustration_path}
              commonName={sp.common_name}
            />
          </div>
        ))}
      </div>

      <style>{`
        @keyframes home-plant-drift {
          0%, 100% { transform: translate(0, 0); }
          50% { transform: translate(6px, -10px); }
        }
        @media (prefers-reduced-motion: reduce) {
          [class*="home-plant"] { animation: none !important; }
        }
      `}</style>
    </section>
  );
}
