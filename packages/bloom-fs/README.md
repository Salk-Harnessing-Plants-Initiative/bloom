# @salk-harnessing-plants-initiative/bloom-fs

Filesystem tools for loading data into Bloom: read cylinder scan images and their
metadata from disk and upload them, upload scRNA datasets, and manage Bloom login
credentials. Built on `@salk-harnessing-plants-initiative/bloom-js`.

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
npm install @salk-harnessing-plants-initiative/bloom-fs
```

This also installs the matching `bloom-js`.

## Usage

Upload a folder of cylinder scan images. The folder, or one of its parent folders, must
contain a `cyl-metadata.yml` describing how image paths map to plants and scans:

```ts
import { SupabaseStore, SupabaseUploader } from '@salk-harnessing-plants-initiative/bloom-js'
import {
  createSupabaseClient,
  getImageMetadata,
  summarizeImageMetadata,
  printSummary,
  uploadImages,
} from '@salk-harnessing-plants-initiative/bloom-fs'

const supabase = await createSupabaseClient('prod') // signs in with ~/.bloom/credentials.txt
const { metadata, paths } = await getImageMetadata('/data/scans/wave1')
printSummary(await summarizeImageMetadata(metadata))

await uploadImages(paths, metadata, new SupabaseUploader(supabase), new SupabaseStore(supabase), {
  nWorkers: 4,
})
```

| Area | Exports |
|---|---|
| Cylinder scans | `initCylMetadata`, `getImageMetadata`, `summarizeImageMetadata`, `printSummary`, `uploadImages`, `getPlantAccessions` |
| scRNA | `uploadSCRNAdata`, `uploadScRNAGenedata`, `uploadScRNACells`, `uploadScRNACounts` |
| Credentials | `createSupabaseClient`, `testCredentials`, `getAvailableProfiles`, `getAnonCredentials`, `getCredentialsPath`, `loadCredentials`, `saveCredentials` |
| Helpers | `processCSV`, `saveToCSV`, `concurrentMap` |

Credentials live in `~/.bloom/`. `credentials.txt` is the `prod` profile, and
`credentials.<name>.txt` is profile `<name>`.

These functions were written for command-line tools. Some print progress to the
console, and some end the process when setup is missing, such as an unknown profile
or no `cyl-metadata.yml`.

## Versions

- `latest`: stable releases, cut from `main`.
- `dev`: `X.Y.Z-dev.N` builds from `staging`. Install one with
  `npm install @salk-harnessing-plants-initiative/bloom-fs@dev`, or pin the exact version.

Changes are listed in [CHANGELOG.md](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/main/packages/bloom-fs/CHANGELOG.md). Releasing is described in
[packages/RELEASE_PROCESS.md](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/main/packages/RELEASE_PROCESS.md).

The V1 package `@salk-hpi/bloom-fs` is a separate line on the V1 GitLab registry.
