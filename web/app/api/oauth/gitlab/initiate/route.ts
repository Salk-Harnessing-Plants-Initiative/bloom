import { NextResponse } from "next/server";
import {
  getUser,
  getSession,
  createServiceRoleSupabaseClient,
} from "@/lib/supabase/server";
import crypto from "crypto";

type OauthFlowState = {
  user_id: string
  provider: string
  state: string
  code_challenge: string
  code_verifier: string
  created_at?: string
}


export async function POST() {
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
  ) as any;
  // hardcode provider as gitlab for now
  const provider = "gitlab";
  // delete existing flows in the oauth_flow table
  const { data: deleteResponse, error: deleteError } = await supabase
    .from("oauth_flow_state")
    .delete()
    .eq("user_id", user.id)
    .eq("provider", provider);
  // return 500 if there is an error.
  if (deleteError) {
    return new NextResponse("Internal Server Error", { status: 500 });
  }
  // generate a code verifier, code challenge, and state
  const code_length = 128;
  const code_verifier = crypto.randomBytes(code_length / 2).toString("hex");
  const code_challenge = crypto
    .createHash("sha256")
    .update(code_verifier)
    .digest("base64url");
  const state_length = 64;
  const state = crypto.randomBytes(state_length / 2).toString("hex");
  // return 500 if there is an error.
  if (!code_challenge) {
    return new NextResponse("Internal Server Error", { status: 500 });
  }
  // insert info into the oauth_flow_state table
  const { data: response, error } = await supabase
    .from("oauth_flow_state")
    .insert([
      {
        user_id: user.id,
        provider,
        state,
        code_challenge,
        code_verifier,
      } as OauthFlowState,
    ])

  // return 500 if there is an error.
  if (error) {
    return new NextResponse("Internal Server Error", { status: 500 });
  }


  // construct the redirect url
  const app_url = process.env.NEXT_PUBLIC_APP_URL;
  const redirect_uri = `${app_url}/api/oauth/${provider}/exchange`;
  const client_id = process.env.GITLAB_CLIENT_ID;
  const scope = "api";
  const authorization_url = `https://gitlab.com/oauth/authorize?client_id=${client_id}&redirect_uri=${redirect_uri}&response_type=code&state=${state}&scope=${scope}&code_challenge=${code_challenge}&code_challenge_method=S256`;

  return NextResponse.json({ authorization_url });
}
