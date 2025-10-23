import { getUser } from "@salk-hpi/bloom-nextjs-auth";
import Link from "next/link";
import Mixpanel from "mixpanel";

export default async function Software() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/software",
  });

  return (
    <div>
      <div className="italic text-xl mb-8 select-none">Software</div>
      <div className="mb-6 select-none">
        To interact with Bloom data programmatically, install the{" "}
        <Link href="https://docs.bloom.salk.edu">
          <span className="capitalize text-lime-700 hover:underline">
            Bloom Command-Line Interface (CLI)
          </span>
        </Link>
        .
      </div>
    </div>
  );
}
