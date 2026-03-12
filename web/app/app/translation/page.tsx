import { getUser } from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

import { createServerSupabaseClient } from "@/lib/supabase/server";
import Link from "next/link";
import { Key, ReactElement, JSXElementConstructor, ReactNode, ReactPortal } from "react";

// import type { TranslationProject } from "@/lib/custom.types";

export default async function Translation() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/translation",
  });

  const projects : any = await getProjectsList();

  return (
    <div>
      <div className="italic text-xl mb-8 select-none">Translation</div>
      <div className="mb-6 select-none">
        Information about different translation projects.
      </div>
      <div>
        <table className="mt-6">
          <thead>
            <tr>
              <th className="text-sm text-neutral-600 text-left">
                Project name
              </th>
              <th className="text-sm text-neutral-600 text-left pl-4">
                Spreadsheet
              </th>
            </tr>
          </thead>
          <tbody>
            {projects?.length === 0 ? (
              <tr>
                <td className="text-sm text-neutral-600 italic">None</td>
                <td className="text-sm text-neutral-600 italic pl-4">None</td>
              </tr>
            ) : (
              projects?.map((project: { id: Key | null | undefined; name: string | number | ReactElement<any, string | JSXElementConstructor<any>> | ReactPortal | Iterable<ReactNode> | null | undefined; spreadsheet_url: any; }) => (
                <tr key={project.id}>
                  <td className="text-sm mt-2">{project.name as React.ReactNode}</td>
                  <td className="text-sm mt-2 pl-4 text-lime-700 hover:underline">
                    <Link href={project.spreadsheet_url || "#"} target="_blank">
                      Link &#x2197;
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {projects?.length === 0 && (
          <div className="text-sm mt-6 text-neutral-600">
            You don't have access to any projects. Email Dan (dbutler@salk.edu)
            to request access.
          </div>
        )}
      </div>
    </div>
  );
}

async function getProjectsList() {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("translation_projects")
    .select("*")
    .order("name", { ascending: true });

  return data;
}
