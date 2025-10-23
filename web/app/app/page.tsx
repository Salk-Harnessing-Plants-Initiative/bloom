import Link from "next/link";
import Mixpanel from "mixpanel";
import { getUser } from "@salk-hpi/bloom-nextjs-auth";

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
      <div className="italic text-xl mb-8">Getting started</div>
      <div className="mb-6">
        Bloom is a web app for sharing data within the Salk Harnessing Plants
        Initiative.
      </div>
      <div>
        <ul className="list-disc ml-8 mb-6">
          <li className="mb-4">
            <span className="font-bold">Explore</span> data using links on the
            left
          </li>
          <li className="mb-4">
            <span className="font-bold">Share</span> data with other Bloom users
            by sharing page URLs
            <div className="mt-1 mb-2 text-sm">
              Example:{" "}
              <Link
                className="text-lime-700 hover:underline"
                href={sampleUrl.href}
              >
                {sampleUrl.text}
              </Link>
            </div>
          </li>
        </ul>
        <div>
          This project is a work in progress, and we appreciate feedback! Please
          send comments or questions to Dan Butler (
          <a
            className="text-lime-700 hover:underline"
            href="mailto:dbutler@salk.edu"
          >
            dbutler@salk.edu
          </a>
          ).
        </div>
      </div>
    </div>
  );
}
