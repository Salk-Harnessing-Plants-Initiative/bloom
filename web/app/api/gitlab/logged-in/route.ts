import { NextRequest, NextResponse } from "next/server";
import {
  getUser,
  createServiceRoleSupabaseClient,
} from "@salk-hpi/bloom-nextjs-auth";
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
  // const { data: response, error } = await supabase
  //   .from("oauth_tokens")
  //   .select("*")
  //   .eq("user_id", user.id)
  //   .eq("provider", provider);
  // if (error) {
  //   return new NextResponse("Not Authorized", { status: 401 });
  // }
  // const logged_in = response !== null && response.length > 0;
  // return NextResponse.json({ logged_in });
  return NextResponse.json({ logged_in: false });
}
