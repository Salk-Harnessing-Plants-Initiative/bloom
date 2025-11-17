import 'dotenv/config';
import { processCSV } from "@salk-hpi/bloom-fs";
import {
    SupabaseStore
} from "@salk-hpi/bloom-js";
import dotenv from 'dotenv';
import { createClient } from '@supabase/supabase-js';
import { mkdirp, outputFile } from 'fs-extra';
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';

const envFile = process.env.NODE_ENV === 'production' ? '.env.prod' : '.env.dev';
dotenv.config({ path: envFile });
console.log(`Loaded ${envFile}`);

// In PROD :  Use NEXT_PUBLIC_SUPABASE_URL (http://localhost/api through nginx)
// In DEV  :   Use SUPABASE_URL (http://localhost:8000 direct to Kong)

const supabaseUrl = process.env.NODE_ENV === 'production' 
  ? process.env.NEXT_PUBLIC_SUPABASE_URL! 
  : process.env.SUPABASE_URL!;
  
const supabaseKey = process.env.SERVICE_ROLE_KEY!;

console.log(`Using Supabase URL: ${supabaseUrl}`);
console.log(`Using SERVICE_ROLE_KEY: ${supabaseKey.substring(0, 20)}...`);

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

/**
 * Creates a new development user account in Supabase using the Admin API.
 *
 * This function:
 *  1. Initializes a Supabase client with admin privileges.
 *  2. Attempts to create a new user with the provided username and password,
 *     using the email format `${user}@salk.edu`.
 *  3. Marks the email as confirmed (`email_confirm: true`) to skip verification for dev users.
 *  4. Gracefully handles cases where the user already exists and logs relevant messages.
 *
 * @async
 * @function addDevUser
 * @param {string} user - The username (without domain) of the developer to create.
 * @param {string} password - The password to assign to the new user.
 * @returns {Promise<void>} Logs success, warning, or error messages to the console.
 */
async function addDevUser(user: string, password: string): Promise<void> {
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

/**
 * Creates or updates a local Bloom credentials file for a given user profile.
 *
 * This function:
 *  1. Builds a `.bloom` directory inside the current user's home directory.
 *  2. Constructs a credentials file path with the provided file extension (e.g., `.dev`, `.prod`).
 *  3. Writes Supabase connection variables (email, password, API URL, anon key)
 *     into the credentials file in key–value format.
 *  4. Ensures the directory exists before writing using `mkdirp` and `outputFile`.
 *
 * @async
 * @function writeProfile
 * @param {string} username - The username (without domain) used to generate the Bloom email.
 * @param {string} password - The password to include in the credentials file.
 * @param {string} ext - The profile extension (e.g., "dev", "prod") used in the file name.
 * @returns {Promise<void>} Creates or overwrites the credentials file; logs no output on success.
 */
async function writeProfile(username: string, password: string, ext: string): Promise<void> {
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

/**
 * Lists all storage buckets in Supabase and performs a simple test upload
 * to verify that the storage service is accessible and writable.
 *
 * This function:
 *  1. Calls `supabase.storage.listBuckets()` to fetch and log all available buckets.
 *  2. Attempts to upload a small text file (`test_upload.txt`) to the "images" bucket
 *     to confirm upload permissions and connectivity.
 *  3. Logs any errors encountered during listing or uploading for debugging.
 *
 * @async
 * @function listBuckets
 * @param {any} supabase - An initialized Supabase client instance.
 * @returns {Promise<void>} Logs results to the console; does not return a value.
 */
async function listBuckets(supabase: any): Promise<void> {
  console.log("Listing storage buckets...");
  const { data, error } = await supabase.storage.listBuckets();
  if (error) {
    console.error('Error listing buckets:', error);
    
  } else {
    console.log('Buckets:', data);
  }

  const testBucket = 'images';
  const testPath = 'sample_cyl_scan/test_upload.txt';
  const testContent = Buffer.from('hello world');

  try {
    const { data, error } = await supabase
      .storage
      .from(testBucket)
      .upload(testPath, testContent, { contentType: 'text/plain', upsert: true });

    if (error) {
      console.error(`Test upload failed:`, error);
    } else {
      console.log(`Test upload succeeded:`, data);
    }
  } catch (err) {
    console.error('Unexpected error during test upload:', err);
  }
}

/**
 * Uploads all .png images from the local sample images directory to a Supabase storage bucket.
 *
 * This function:
 *  1. Reads all .png files from `sampleImagesDir`.
 *  2. Builds each file’s destination path inside the target bucket (using `imagesPathPrefix`).
 *  3. Uploads each file to Supabase Storage with content type "image/png", overwriting existing files if needed.
 *  4. Logs upload progress, success, and error details for debugging.
 *
 * @async
 * @function uploadSampleImages
 * @throws Logs errors if reading files or uploading fails.
 */
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

/**
 * Loads test data from CSV files into Supabase tables in a defined order.
 *
 * This function:
 *  1. Iterates through each table name in `table_load_order`.
 *  2. Looks for a corresponding CSV file in the `../test_data/` directory (e.g., `table.csv`).
 *  3. For each existing CSV, reads its rows and inserts them into the matching Supabase table.
 *  4. Logs progress, warnings for missing CSV files, and any insert errors encountered.
 *
 * @async
 * @function loadTestTables
 * @param {any} supabase - An initialized Supabase client instance.
 * @returns {Promise<void>} Logs progress and errors to the console; does not return a value.
 *
 */
async function loadTestTables(supabase: any): Promise<void> {
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
    
    console.log("Testing Supabase storage connectivity...");
    await listBuckets(supabase);

    console.log("Uploading Scanned Images to sample_cyl_scan/ path on S3 bucket...");
    await uploadSampleImages();
    
    console.log("Loading test tables into Supabase...");
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

