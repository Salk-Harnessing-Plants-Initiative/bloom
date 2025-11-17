export const runtime = 'nodejs';
import { NextRequest, NextResponse } from "next/server";
import {
  getUser,
  createServiceRoleSupabaseClient,
} from "@/lib/supabase/server";
import { encryptToken, decryptToken } from "@salk-hpi/bloom-js";

export async function GET(request: NextRequest) {
  const user = await getUser();
  // return 401 if user is not logged in.
  if (!user) {
    return new NextResponse("Unauthorized", { status: 401 });
  }
  // get the supabase_url and service_role_key from the environment variables
  const supabase_url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const service_role_key = process.env.SERVICE_ROLE_KEY;
  // return 500 if supabase_url or service_role_key is not set.
  if (!supabase_url || !service_role_key) {
    return new NextResponse("Internal Server Error", { status: 500 });
  }
  // create a supabase client with the service role key
  const supabase = createServiceRoleSupabaseClient(
    supabase_url,
    service_role_key
  );
  // hardcode provider as gitlab for now
  const provider = "gitlab";
  // get encrypted access token from the database
  type OAuthTokenRow = {
    encrypted_access_token: string;
    // add other fields if needed
  };

  // const { data: response, error } = (await supabase
  // .from("oauth_tokens")
  // .select("*")
  // .eq("user_id", user.id)
  // .eq("provider", provider)
  // .single()) as {
  //   data: { encrypted_access_token: string } | null
  //   error: any
  // }

  // if (error || !response) {
  //   return new NextResponse("Not Authorized", { status: 401 });
  // }
  // decrypt the access token
  
  // const access_token = decryptToken(response.encrypted_access_token);
  // get gitlab user from API
  // const gitlab_user_url = "https://gitlab.com/api/v4/user";
  // const gitlab_headers = {
  //   Authorization: `Bearer ${access_token}`,
  // };
  // const gitlab_user_response = await fetch(gitlab_user_url, {
  //   headers: gitlab_headers,
  // }).then((res) => res.json());
  // const user_id = gitlab_user_response.id;
  // // get gitlab projects from API (set membership to true)
  // const gitlab_projects_url = `https://gitlab.com/api/v4/projects?membership=true`;
  // const gitlab_projects_response = await fetch(gitlab_projects_url, {
  //   headers: gitlab_headers,
  // }).then((res) => res.json());
  // return NextResponse.json(gitlab_projects_response);
  return NextResponse.json(null);
}
