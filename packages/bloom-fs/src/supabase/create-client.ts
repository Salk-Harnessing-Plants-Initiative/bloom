import { createClient } from "@supabase/supabase-js";
import { LocalStorage } from "./local-storage";
import { Database } from "../types/database.types";
import { loadCredentials } from "./credentials";

import * as os from "os";
import * as path from "path";
import * as glob from "glob";

export function getAvailableProfiles() {
  const bloomConfigDir = path.join(os.homedir(), ".bloom");

  // glob for bloomConfigDir/credentials.txt and bloomConfigDir/credentials.*.txt
  const files = glob.sync(path.join(bloomConfigDir, "credentials*.txt").replace(/\\/g, "/"));

  // extract files of the form credentials.txt or credentials.*.txt
  const profiles = files.map((file) => {
    const match = file.match(/credentials(?:\.(.*))?\.txt/);
    const name = match && match[1] ? match[1] : "prod";
    return { name, file };
  });

  // if "prod" occurs twice, raise an error
  const profileNames = profiles.map((profile) => profile.name);
  const uniqueProfileNames = new Set(profileNames);
  if (uniqueProfileNames.size !== profileNames.length) {
    console.error(
      "Error: There should only be ~/.bloom/credentials.prod.txt or ~/.bloom/credentials.txt, not both."
    );
    process.exit(1);
  }

  return profiles;
}

export async function createSupabaseClient(profile: string) {
  const localStorage = new LocalStorage();
  const clientOptions = {
    auth: {
      autoRefreshToken: true,
      persistSession: true,
      storage: localStorage,
    },
  };

  const availableProfiles = getAvailableProfiles();
  const path = availableProfiles.find((p) => p.name === profile)?.file;

  if (!path) {
    console.error(`Error: Profile ${profile} not found.`);
    process.exit(1);
  }
  const credentials = loadCredentials(path);

  const supabase = createClient<Database>(
    credentials.api_url,
    credentials.anon_key,
    clientOptions
  );
  const { error } = await supabase.auth.signInWithPassword({
    email: credentials.email,
    password: credentials.password,
  });
  if (error) {
    throw error;
  }
  return supabase;
}

export async function testCredentials(credentials: {
  email: string;
  password: string;
  api_url: string;
  anon_key: string;
}) {
  const localStorage = new LocalStorage();
  const clientOptions = {
    auth: {
      autoRefreshToken: true,
      persistSession: true,
      storage: localStorage,
    },
  };
  const supabase = createClient<Database>(
    credentials.api_url,
    credentials.anon_key,
    clientOptions
  );
  const { error } = await supabase.auth.signInWithPassword({
    email: credentials.email,
    password: credentials.password,
  });
  return { error };
}
