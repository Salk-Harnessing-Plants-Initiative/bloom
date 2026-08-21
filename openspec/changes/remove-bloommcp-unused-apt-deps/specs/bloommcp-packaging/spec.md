## ADDED Requirements

### Requirement: Minimal Runtime Image (No Unused Build Toolchain)

The `bloommcp` `Dockerfile` SHALL NOT install system-level build tooling (compilers,
`pkg-config`, or `-dev` header packages via `apt-get`) unless a currently pinned
dependency actually requires compiling from source for a target platform the image is
built for. Dependencies that publish prebuilt wheels (`manylinux`/`musllinux` matching
the pinned Python ABI and target architectures) SHALL be installed via `uv sync` alone,
with no accompanying `apt-get` block.

#### Scenario: Image builds without a compiler toolchain

- **WHEN** the `bloommcp` image is built from `Dockerfile` via `uv sync --frozen
  --no-dev --no-cache`
- **THEN** no `apt-get install` step installs a compiler (e.g. `gcc`) or native
  `-dev` header packages, and the build still completes by resolving prebuilt wheels
  for matplotlib, numpy, and scipy
