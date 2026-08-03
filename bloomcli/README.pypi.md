# bloomctl

Command-line tool for the Bloom Database (Salk HPI) — log in, download cylinder
experiments (metadata + images), and work with cylinder trait datasets.

## Install

`bloomctl` is published to PyPI as **`bloomctl`**. Current releases are
pre-releases (`0.1.0aN`), so opt in explicitly:

```bash
uv tool install bloomctl --prerelease=allow    # isolated CLI tool (recommended)
uvx --prerelease=allow bloomctl --help         # one-off, no install
pip install --pre bloomctl                     # into the active environment
```

Verify it:

```bash
bloomctl --version
```

## Getting started

Authenticate once, then run commands against Bloom:

```bash
bloomctl login
```

`login` prompts for your Bloom email + password, bootstraps the server's public
client config, and writes credentials to `~/.bloom/credentials.txt`. Use
`-p/--profile <name>` to keep separate logins side by side (the default profile
is `prod`), and pass the same `--profile` to any command.

Run `bloomctl --help` or `bloomctl <command> --help` for the full option list.

## Commands

- `bloomctl login` — log in and store credentials per profile.
- `bloomctl cyl download <out_dir> …` — download a cylinder experiment or single
  scan (metadata `scans.csv` + per-frame images).
- `bloomctl cyl download-for-predict <scan-id> <out>` — stage one scan into the
  predict-ready layout for the pipeline.
- `bloomctl cyl ingest-result <envelope>` — write a per-scan pipeline result back
  to Bloom (needs write access).
- `bloomctl cyl datasets list | get <name> | create <name> <experiment_id> <trait_source_name>`
  — list, inspect, or create cylinder trait datasets.
- `bloomctl cyl experiments list` — list cylinder experiments (`--species` to pick
  one from a menu; `--output csv|json` to grab an id for `cyl download`).
- `bloomctl cyl accessions list | sample-counts` — list the accessions in an
  experiment, or the plant count per accession per species. Pick the experiment /
  species from a menu, or pass `--experiment-id` (`accessions list` stays scriptable).
- `bloomctl cyl qc list-sets` — list cylinder QC (quality-control) sets
  (`--output csv|json` for machine-readable output).

Read commands work for any logged-in user; write commands (`ingest-result`,
`datasets create`) require an account with write access.

## Documentation

Full docs — including the container image, access roles, and per-command
details — are in the
[project repository](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/tree/main/bloomcli).
