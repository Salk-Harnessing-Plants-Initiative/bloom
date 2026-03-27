import * as os from 'os'
import * as path from 'path'
import * as fs from 'fs'
import * as dotenv from 'dotenv'

export async function getAnonCredentials(server: string) {
  const response = await fetch(`${server}/api/client-info`)
  const { api_url, anon_key } = (await response.json()) as {
    api_url: string
    anon_key: string
  }
  return { api_url, anon_key }
}

export function getCredentialsPath(local: boolean = false) {
  const filename = local ? 'credentials.local.txt' : 'credentials.txt'
  const filepath = path.join(os.homedir(), '.bloom', filename)
  return filepath
}

export function checkCredentialsSaved(local: boolean = false) {
  const filepath = getCredentialsPath(local)
  return fs.existsSync(filepath)
}

type Credentials = {
  email: string
  password: string
  api_url: string
  anon_key: string
}

export function saveCredentials(filepath: string, credentials: Credentials) {
  const contents =
    `BLOOM_EMAIL=${credentials.email}\n` +
    `BLOOM_PASSWORD=${credentials.password}\n` +
    `BLOOM_API_URL=${credentials.api_url}\n` +
    `BLOOM_ANON_KEY=${credentials.anon_key}\n`

  const dir = path.dirname(filepath)
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true })
  }
  console.log(`Saving credentials to ${filepath}`)
  fs.writeFileSync(filepath, contents)
}

export function loadCredentials(filepath: string) {
  // const local = parseInt(process.env.BLOOM_USE_LOCAL || "0") > 0;
  // if (!checkCredentialsSaved(local)) {
  //   throw new Error(
  //     `Not logged in. Run "bloom login ${local ? "-l" : ""}" to log in.`
  //   );
  // }

  // const filepath = getCredentialsPath(local);
  dotenv.config({ path: filepath })

  const credentials = {
    email: process.env.BLOOM_EMAIL || '',
    password: process.env.BLOOM_PASSWORD || '',
    api_url: process.env.BLOOM_API_URL || '',
    anon_key: process.env.BLOOM_ANON_KEY || '',
  }

  // console.log(`Bloom API URL: ${credentials.api_url}\n`);

  return credentials
}
