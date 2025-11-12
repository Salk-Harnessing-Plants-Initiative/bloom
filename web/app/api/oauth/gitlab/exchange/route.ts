import { NextRequest, NextResponse } from 'next/server'
import { getUser, getSession, createServiceRoleSupabaseClient } from '@salk-hpi/bloom-nextjs-auth'
import { encryptToken } from '@salk-hpi/bloom-js'
import crypto from 'crypto'

type GitlabTokenResponse = {
  access_token: string
  token_type: string
  expires_in: number
  refresh_token: string
  created_at: number
}

type OauthFlowState = {
  id: string
  user_id: string
  provider: string
  state: string
  code_verifier: string
  created_at?: string
}

type OauthTokens = {
  user_id: string
  provider: string
  encrypted_access_token: string
  encrypted_refresh_token: string
  created_at: string
  expires_at: string
}

export async function GET(request: NextRequest) {
  // const user = await getUser();
  // // return 401 if user is not logged in.
  // if (!user) {
  //   return new NextResponse("Unauthorized", { status: 401 });
  // }
  // // get the supabase_url and service_role_key from the environment variables
  // const supabase_url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  // const service_role_key = process.env.SERVICE_ROLE_KEY;
  // // return 500 if supabase_url or service_role_key is not set.
  // if (!supabase_url || !service_role_key) {
  //   return new NextResponse("Internal Server Error", { status: 500 });
  // }
  // // create a supabase client with the service role key
  // const supabase = createServiceRoleSupabaseClient(
  //   supabase_url,
  //   service_role_key
  // ) as ReturnType<typeof createServiceRoleSupabaseClient> & {
  //   from(table: "oauth_tokens"): {
  //     insert(values: OauthTokens[]): Promise<{ data: any; error: any }>;
  //     delete(): any;
  //     eq(column: string, value: any): any;
  //     select(columns: string): any;
  //     single(): any;
  //   };
  //   from(table: "oauth_flow_state"): {
  //     select(columns: string): any;
  //     eq(column: string, value: any): any;
  //     single(): any;
  //   };
  // };
  // // hardcode provider as gitlab for now
  // const provider = "gitlab";

  // // get the code and state from the query parameters
  // const code = request.nextUrl.searchParams.get("code") || "";
  // const state = request.nextUrl.searchParams.get("state") || "";

  // // get data from the oauth_flow_state table
  // const { data: response, error } = (await supabase
  //   .from("oauth_flow_state")
  //   .select("*")
  //   .eq("user_id", user.id)
  //   .eq("provider", provider)
  //   .eq("state", state)
  //   .single()) as {
  //     data: OauthFlowState | null
  //     error: any
  //   }

  // // return 500 if there is an error.
  // if (error || !response) {
  //   return new NextResponse("Internal Server Error", { status: 500 });
  // }

  // // construct POST request to get the access token
  // const gitlab_url = "https://gitlab.com/oauth/token";
  // const client_id = process.env.GITLAB_CLIENT_ID || "";
  // const client_secret = process.env.GITLAB_CLIENT_SECRET || "";

  // const app_url = process.env.NEXT_PUBLIC_APP_URL || "";
  // const redirect_uri = `${app_url}/api/oauth/${provider}/exchange`;
  // const code_verifier = response.code_verifier;
  // const grant_type = "authorization_code";
  // const parameters = {
  //   client_id,
  //   client_secret,
  //   code,
  //   grant_type,
  //   redirect_uri,
  //   code_verifier,
  // };

  // const body = new URLSearchParams(parameters).toString();
  // const headers = {
  //   "Content-Type": "application/x-www-form-urlencoded",
  // };

  // const gitlab_response: GitlabTokenResponse = await fetch(gitlab_url, {
  //   method: "POST",
  //   body,
  //   headers,
  // }).then((res) => res.json());

  // // insert info from gitlab_response into the oauth_tokens table

  // const encrypted_access_token = encryptToken(gitlab_response.access_token);
  // const encrypted_refresh_token = encryptToken(gitlab_response.refresh_token);

  // const created_at = new Date(gitlab_response.created_at * 1000).toISOString();
  // const expires_at = new Date(
  //   Date.now() + gitlab_response.expires_in * 1000
  // ).toISOString();

  // // delete existing tokens in the oauth_tokens table
  // const { data: deleteResponse, error: deleteError } = await supabase
  //   .from("oauth_tokens")
  //   .delete()
  //   .eq("user_id", user.id)
  //   .eq("provider", provider);

  // // return 500 if there is an error.
  // if (deleteError) {
  //   return new NextResponse("Internal Server Error", { status: 500 });
  // }

  // // insert info into the oauth_tokens table
  // const { data: insertResponse, error: insertError } = await supabase
  //   .from("oauth_tokens")
  //   .insert([
  //     {
  //       user_id: user.id,
  //       provider,
  //       encrypted_access_token,
  //       encrypted_refresh_token,
  //       created_at,
  //       expires_at,
  //     },
  //   ])

  // // return 500 if there is an error.
  // if (insertError) {
  //   return new NextResponse("Internal Server Error", { status: 500 });
  // }

  // const finalRedirect = new URL("/app/pipelines", app_url);
  // return NextResponse.redirect(finalRedirect);
  return NextResponse.redirect('/')
}
