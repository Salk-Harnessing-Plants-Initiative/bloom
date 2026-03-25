import Link from "next/link";
import Mixpanel from "mixpanel";
import { getUser } from "@/lib/supabase/server";

export default async function Index() {
  const domain = process.env.NEXT_PUBLIC_BLOOM_URL;
  const path = "app/phenotypes/1/6/PI458606";
  const sampleUrl = {
    href: `${domain}/${path}`, // the domain is set in the .env file
    text: `https://bloom.salk.edu/${path}`, // always display bloom.salk.edu as the domain
  };

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app",
  });

  return (
    <div className="w-[650px]">
      <div className="text-xl font-semibold text-neutral-800 mb-8">Getting started</div>
      <div className="mb-6 text-neutral-600 leading-relaxed">
        Bloom is a web app for sharing data within the Salk Harnessing Plants
        Initiative.
      </div>
      <div>
        <ul className="list-disc ml-8 mb-6">
          <li className="mb-4 text-neutral-600">
            <span className="font-semibold text-neutral-800">Explore</span> data using links on the
            left
          </li>
          <li className="mb-4 text-neutral-600">
            <span className="font-semibold text-neutral-800">Share</span> data with other Bloom users
            by sharing page URLs
            <div className="mt-1.5 mb-2 text-sm text-neutral-500">
              Example:{" "}
              <Link
                className="text-lime-700 hover:text-lime-800 hover:underline transition-colors"
                href={sampleUrl.href}
              >
                {sampleUrl.text}
              </Link>
            </div>
          </li>
        </ul>
        {/* Section Cards */}
        <div className="grid grid-cols-2 gap-3 mb-8">
          <Link href="/app/phenotypes" className="block p-4 rounded-lg border border-stone-200 bg-white hover:border-lime-300 hover:shadow-sm transition-all">
            <div className="font-semibold text-neutral-800 text-sm">Phenotypes</div>
            <div className="text-xs text-neutral-500 mt-1">Cylinder experiment data and plant scans</div>
          </Link>
          <Link href="/app/traits" className="block p-4 rounded-lg border border-stone-200 bg-white hover:border-lime-300 hover:shadow-sm transition-all">
            <div className="font-semibold text-neutral-800 text-sm">Traits</div>
            <div className="text-xs text-neutral-500 mt-1">Time-series imaging traits and measurements</div>
          </Link>
          <Link href="/app/genes" className="block p-4 rounded-lg border border-stone-200 bg-white hover:border-lime-300 hover:shadow-sm transition-all">
            <div className="font-semibold text-neutral-800 text-sm">Genes</div>
            <div className="text-xs text-neutral-500 mt-1">Gene information, metadata, and search</div>
          </Link>
          <Link href="/app/expression" className="block p-4 rounded-lg border border-stone-200 bg-white hover:border-lime-300 hover:shadow-sm transition-all">
            <div className="font-semibold text-neutral-800 text-sm">Expression</div>
            <div className="text-xs text-neutral-500 mt-1">Single-cell RNA sequencing visualization</div>
          </Link>
          <Link href="/app/timeline" className="block p-4 rounded-lg border border-stone-200 bg-white hover:border-lime-300 hover:shadow-sm transition-all">
            <div className="font-semibold text-neutral-800 text-sm">Timeline</div>
            <div className="text-xs text-neutral-500 mt-1">Growth progression and time-series views</div>
          </Link>
          <Link href="/chat" className="block p-4 rounded-lg border border-stone-200 bg-white hover:border-lime-300 hover:shadow-sm transition-all">
            <div className="font-semibold text-neutral-800 text-sm">Bloom Assistant</div>
            <div className="text-xs text-neutral-500 mt-1">Ask questions about your data using AI</div>
          </Link>
        </div>

        <div className="text-neutral-500 text-sm">
          This project is a work in progress, and we appreciate feedback!
        </div>
      </div>
    </div>
  );
}
