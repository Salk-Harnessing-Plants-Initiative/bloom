# @salk-harnessing-plants-initiative/bloom-js

Shared TypeScript building blocks for Bloom: typed Supabase access for the Bloom
database and storage, species loading, and OAuth token encryption.
`@salk-harnessing-plants-initiative/bloom-fs` builds on it.

## Install

The package is private on GitHub Packages. Add to your project's `.npmrc`:

```ini
@salk-harnessing-plants-initiative:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

Set `NODE_AUTH_TOKEN` to a GitHub token that can read the organisation's packages
(a classic personal access token with `read:packages`, or `GITHUB_TOKEN` with
`packages: read` in GitHub Actions), then:

```bash
npm install @salk-harnessing-plants-initiative/bloom-js @supabase/supabase-js
```

`@supabase/supabase-js` is a peer dependency: you create the client, and bloom-js uses it.

## Usage

```ts
import { createClient } from '@supabase/supabase-js'
import {
  SupabaseStore,
  SupabaseUploader,
  createSpeciesBulk,
  loadSpeciesData,
  type TypedSupabaseClient,
} from '@salk-harnessing-plants-initiative/bloom-js'

const supabase: TypedSupabaseClient = createClient(apiUrl, anonKey)
const store = new SupabaseStore(supabase) // database reads and writes
const uploader = new SupabaseUploader(supabase) // storage uploads

const species = await loadSpeciesData('species.yml')
await createSpeciesBulk(species, uploader, store)
```

| Export | What it does |
|---|---|
| `SupabaseStore` | Reads and writes species, experiments, cylinder scanners, image metadata and scRNA data |
| `SupabaseUploader` | Uploads images to a storage bucket, with optional PNG compression |
| `loadSpeciesData`, `createSpecies`, `createSpeciesBulk` | Load species from a YAML file and add them with their images |
| `encryptToken`, `decryptToken` | AES-256 encryption for stored OAuth tokens |
| `TypedSupabaseClient`, `SpeciesData` | Types |

`encryptToken` and `decryptToken` read `OAUTH_TOKEN_ENCRYPTION_KEY` (64 hex characters)
when the module loads, so set it before importing.

## Versions

- `latest`: stable releases, cut from `main`.
- `dev`: `X.Y.Z-dev.N` builds from `staging`. Install one with
  `npm install @salk-harnessing-plants-initiative/bloom-js@dev`, or pin the exact version.

Changes are listed in [CHANGELOG.md](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/main/packages/bloom-js/CHANGELOG.md). Releasing is described in
[packages/RELEASE_PROCESS.md](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/main/packages/RELEASE_PROCESS.md).

The V1 package `@salk-hpi/bloom-js` is a separate line on the V1 GitLab registry.
