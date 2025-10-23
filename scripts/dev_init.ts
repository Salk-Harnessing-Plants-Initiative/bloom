import 'dotenv/config';
import { createSupabaseClient, processCSV } from "@salk-hpi/bloom-fs";
import {
    SupabaseUploader,
    SupabaseStore
} from "@salk-hpi/bloom-js";

import { createClient } from '@supabase/supabase-js';

import { mkdirp, outputFile } from 'fs-extra';
import * as os from 'os';
import * as path from 'path';

const supabaseUrl = "http://localhost:8000"
const supabaseKey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiaWF0IjoxNzYwNDA3NTYzLCJleHAiOjIwNzU5ODM1NjN9.MQtGFnfpIKzWTvUIDTH7IUyym8TXDW_kjcWcl-_LNgA";

const table_load_order = [
    "species",
    "people",
    "assemblies",
    "genes",
    "gene_candidates",
    "gene_candidate_scientists",
    "gene_candidate_support",
    "cyl_trait_sources",
    "cyl_scanners"
]



async function addDevUser(user: string, password: string) {
    try {
        // Using the auth.admin.createUser method to add a new user
        const supabase = createClient(
            supabaseUrl,
            supabaseKey,
            { auth: { persistSession: false } }
        );
        const { data, error } = await supabase.auth.admin.createUser({
            email: `${user}@salk.edu`,
            password: password,
            email_confirm: true
        });

        if (error) {
            if (error.status === 422 && error.message.includes("already been registered")) {
                console.warn(`User with email ${user}@salk.edu already exists. Skipping creation.`);
            } else {
                throw error; // Re-throw other types of errors
            }
        } else {
            console.log('User created successfully:', data);
        }
    } catch (error) {
        console.error('Error creating user:', error);
    }
}


async function writeProfile(username: string, password: string, ext: string) {
    const bloomDir = path.join(os.homedir(), '.bloom');
    const filePath = path.join(bloomDir, `credentials.${ext}.txt`);
    const content = "" +
        `BLOOM_EMAIL=${username}@salk.edu\n` +
        `BLOOM_PASSWORD=${password}\n` +
        `BLOOM_API_URL=${supabaseUrl}\n` +
        `BLOOM_ANON_KEY=${supabaseKey}\n`;
    await mkdirp(bloomDir);
    await outputFile(filePath, content);
}


async function main() {
    await writeProfile("testuser5", "testuser5", "dev1");
    await writeProfile("testuser6", "testuser6", "dev2");
    await addDevUser("testuser5", "testuser5");
    await addDevUser("testuser6", "testuser6");

    const supabase = createClient(
        supabaseUrl,
        supabaseKey,
        { auth: { persistSession: false } }
    );

    // const supabase = await createSupabaseClient("dev1");
    // console.log(supabase)
    // const uploader = new SupabaseUploader(supabase);
    const db = new SupabaseStore(supabase);
    for (const name of table_load_order) {
        await processCSV(`test_data/${name}.csv`, async (row) => {
            // code to insert this row into `name` table
            try {
                // Insert each row into the corresponding table
                const { error } = await db.supabase
                    .from(name as any)
                    .insert([row]);

                if (error) {
                    console.error(`Error inserting into ${name}:`, error);
                } else {
                    console.log(`Successfully inserted row into ${name}`);
                }
            } catch (err) {
                console.error(`Error processing CSV for ${name}:`, err);
            }
        })
    }
}

main()
