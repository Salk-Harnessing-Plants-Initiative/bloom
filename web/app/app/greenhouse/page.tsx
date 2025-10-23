import { getUser } from "@salk-hpi/bloom-nextjs-auth";
import Mixpanel from "mixpanel";

export default async function Greenhouse() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/greenhouse",
  });

  return (
    <div>
      <div className="italic text-xl mb-8 select-none">Greenhouse</div>
      <div className="mb-6 select-none">
        Information about sensor readings at the greenhouse - temperature,
        light, and humidity.
      </div>
    </div>
  );
}
