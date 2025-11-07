import 'dotenv/config';
import { createSupabaseClient, processCSV } from "@salk-hpi/bloom-fs";
import {
    SupabaseUploader,
    SupabaseStore
} from "@salk-hpi/bloom-js";
import dotenv from 'dotenv';
import { createClient } from '@supabase/supabase-js';
import { mkdirp, outputFile } from 'fs-extra';
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';
import { fileURLToPath } from "url";

const envFile = process.env.NODE_ENV === 'production' ? '.env.prod' : '.env.dev';
dotenv.config({ path: envFile });
console.log(`Loaded ${envFile}`);

const supabaseUrl = process.env.SUPABASE_URL!;
const supabaseKey = process.env.SUPABASE_KEY!;
const supabase = createClient(supabaseUrl, supabaseKey);

const bucketName = "images";
const sampleImagesDir = "../test_data/sample_cyl_scan/";
const imagesPathPrefix = "sample_cyl_scan/";

const table_load_order = [
    "species",
    "people",
    "assemblies",
    "genes",
    "cyl_experiments",
    "cyl_waves",
    "accessions",
    "cyl_camera_settings",
    "cyl_plants",
    "phenotypers",
    "cyl_scientists",
    "cyl_scans",
    "cyl_images",
    "gene_candidates",
    "gene_candidate_scientists",
    "gene_candidate_support",
    "cyl_trait_sources",
    "cyl_scanners"
]

async function addDevUser(user: string, password: string) {
    try {
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
                throw error;
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

async function listBuckets(supabase: any) {
  console.log("Listing storage buckets...");
  const { data, error } = await supabase.storage.listBuckets();
  if (error) {
    console.error('Error listing buckets:', error);
    
  } else {
    console.log('Buckets:', data);
  }

  const testBucket = 'images';
  const testPath = 'sample_cyl_scan/test_upload.png';
  const testContent = Buffer.from('hello world');

  try {
    const { data, error } = await supabase
      .storage
      .from(testBucket)
      .upload(testPath, testContent, { contentType: 'image/png', upsert: true });

    if (error) {
      console.error(`Test upload failed:`, error);
    } else {
      console.log(`Test upload succeeded:`, data);
    }
  } catch (err) {
    console.error('Unexpected error during test upload:', err);
  }
}

async function uploadSampleImages() {
  try {

    const files = fs.readdirSync(sampleImagesDir).filter(f => f.endsWith(".png"));
    for (const file of files) {
      const localPath = path.join(sampleImagesDir, file);
      const objectPath = `${imagesPathPrefix}${file}`;
      console.log(`Uploading ${file} → ${bucketName}/${objectPath}`);

      const fileData = fs.readFileSync(localPath);

      const { data, error } = await supabase
        .storage
        .from(bucketName)
        .upload(objectPath, fileData, { contentType: "image/png", upsert: true });

      if (error) console.error(`Failed to upload ${file}:`, error);
      else console.log(`Uploaded ${file} successfully`);
    }

    console.log("Finished uploading images!");
  } catch (err) {
    console.error("Upload process failed:", err);
  }
}


async function loadTestTables(supabase: any) {
  const db = new SupabaseStore(supabase);
  for (const name of table_load_order) {
    const csvPath = `../test_data/${name}.csv`;
    if (!fs.existsSync(csvPath)) {
      console.warn(` CSV not found for ${name}, skipping`);
      continue;
    }
    console.log(`Loading table: ${name}`);
    await processCSV(csvPath, async (row: Record<string, any>): Promise<void> => {
      const { error }: { error: any } = await db.supabase.from(name as any).insert([row]);
      if (error) console.error(`Error inserting into ${name}:`, error);
    });
  }
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
    // await listBuckets(supabase);
    // await uploadSampleImages();
    await loadTestTables(supabase);

    const db = new SupabaseStore(supabase);
    for (const name of table_load_order) {
        await processCSV(`test_data/${name}.csv`, async (row) => {
            try {
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

