# npm Release Process

How to publish `@salk-harnessing-plants-initiative/bloom-js` and
`@salk-harnessing-plants-initiative/bloom-fs` to GitHub Packages.
`@salk-hpi/bloom-nextjs-auth` is not published.

The V1 packages (`@salk-hpi/bloom-js`, `@salk-hpi/bloom-fs`) stay on the V1 GitLab
registry for the software that still uses them. The new scope keeps the two lines apart.

## Channels

| Channel | Version | Cut from | GitHub Release | Install with |
|---|---|---|---|---|
| Stable | `X.Y.Z` | `main` | normal release | `npm install <pkg>` (`latest`) |
| Dev | `X.Y.Z-dev.N` | `staging` | marked **pre-release** | `npm install <pkg>@dev`, or pin the exact version |

The release workflow refuses a stable version whose commit is not on `main`, a dev
version whose commit is not on `staging`, a mismatched pre-release flag, and any other
pre-release shape (`-rc.N`, `-beta`, ...).

## Cutting a release

1. From `staging`, run Actions → **version-npm**. Pick the package and the bump:
   `prerelease` gives the next `-dev.N`, and `patch`/`minor`/`major` give a stable version.
   It opens a PR against `staging` with the new version and `package-lock.json`.
2. In that PR, add a `## [X.Y.Z]` entry to `packages/<package>/CHANGELOG.md`. Merge it.
3. Create a GitHub Release tagged `<package>-vX.Y.Z` (e.g. `bloom-js-v0.3.1-dev.0`):
   - **dev:** target `staging` and tick "Set as a pre-release".
   - **stable:** wait until the bump is promoted to `main`, then target `main`.
4. Publishing the Release runs `.github/workflows/release-npm.yml`. It checks the tag,
   channel and changelog, builds the package, installs the packed tarball in an empty
   project, and then publishes. Running the workflow by hand is a dry run.

### bloom-js before bloom-fs

`bloom-fs` depends on `bloom-js`, and its release check installs `bloom-js` from GitHub
Packages. Publish the `bloom-js` version it needs first. Bumping `bloom-js` also moves
`bloom-fs` onto `^<new version>` in the same PR, so release `bloom-fs` afterwards.

## Installing in another project

The packages are private. Each developer and each CI job needs a token that can read
packages in the `Salk-Harnessing-Plants-Initiative` organisation.

`.npmrc` in the consuming project (checked in, no secret):

```ini
@salk-harnessing-plants-initiative:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

- **Developers:** a classic personal access token with `read:packages`, exported as
  `NODE_AUTH_TOKEN`.
- **GitHub Actions in another repo:** use that repo's `GITHUB_TOKEN` with
  `permissions: packages: read`, after granting the repo access (see below).

## One-time setup

1. Create a `github-packages` environment in this repo (Settings → Environments). Add
   required reviewers if publishing should wait for approval.
2. After the first publish of each package, open it under the organisation's
   **Packages** tab → Package settings:
   - Confirm visibility is **Private**.
   - The package is linked to this repo through `repository` in `package.json` and
     may inherit the repo's access. This repo is public, so check who can read the
     package. If needed, turn off inherited access and grant teams explicitly.
   - Under **Manage Actions access**, add each repo whose CI installs the package
     (e.g. bloom-desktop) with the Read role.
