## Why

`bloommcp/Dockerfile:11-17`'s `apt-get` layer installs `libfreetype6-dev`, `libpng-dev`,
`libjpeg-dev`, `pkg-config`, and `gcc`, per its inline comment "System deps for
matplotlib rendering and scipy" — i.e. a full native build toolchain so matplotlib and
scipy can compile from source. Checking `bloommcp/uv.lock` against the currently pinned
versions (matplotlib 3.10.8, numpy 2.4.4, scipy 1.17.1, and every other native-code
package the dependency tree pulls in transitively — scikit-learn, statsmodels, pillow,
contourpy, kiwisolver, fonttools, numba, llvmlite) shows every one of them publishes
`manylinux_2_27`/`manylinux2014` wheels for cp311 on both `x86_64` and `aarch64` — the
exact platforms the digest-pinned `python:3.11-slim` (Debian, glibc) base image runs on.
`uv sync --frozen` should therefore resolve prebuilt wheels, not invoke `gcc`, making the
block dead weight: it adds ~870s and ~215MB of packages (`gcc-14`, `binutils`,
`libc6-dev`, and 47 more) to a container that processes externally-influenced
plotting/analysis inputs, for no runtime benefit.

A sibling design doc (`add-bloomcli-container-release/design.md` Decision 2) already
used wheel-availability as its reasoning for a *different* image: it explains why
`bloomcli`'s own Dockerfile omits an `apt-get` block (`cryptography` ships manylinux
wheels, and nothing else in `bloomcli`'s dependency set touches native code) — but that
same doc takes `bloommcp`'s own apt-get need at face value, citing only the Dockerfile's
inline comment rather than checking wheel availability for matplotlib/scipy itself. This
change performs that check for `bloommcp` and closes the gap (issue #590).

Two dependencies were checked specifically because they could plausibly need a compiler
or runtime-linked system library even with wheels installed, and both check out clean:
- **matplotlib**: its manylinux wheel statically vendors its own FreeType / libpng /
  libjpeg-turbo / zlib copies (compiled in and linked directly into the extension
  module) — it doesn't `dlopen` a system `libfreetype6` / `libpng16` / `libjpeg` at
  runtime, so removing the `-dev` headers changes nothing at runtime.
- **Pillow**: its manylinux wheel bundles the same set of libraries, but as
  dynamically-linked `.so`s that `auditwheel` vendors into the wheel itself and
  rewrites to load via an RPATH-relative path (the standard manylinux
  "batteries-included" convention) — not statically linked, but still self-contained
  and never resolved against a system `libfreetype6` / `libpng16` / `libjpeg`, so the
  same "no `-dev` headers needed at runtime" conclusion holds.
- **numba / llvmlite** (pulled in transitively via `umap-learn`/`pynndescent`, used by
  `sleap_roots_umap_analysis`): their JIT (`@numba.jit`) compiles in-process through a
  statically-bundled LLVM via llvmlite's wheel — it does not shell out to `gcc`/`cc`, so
  it needs no compiler at runtime either. (The one numba path that *does* need an
  external compiler, `numba.pycc` ahead-of-time compilation, is not used anywhere in
  `bloommcp`.)

**Verified on both target architectures.** A `--no-cache` build against the current
Dockerfile was run locally on Apple Silicon (`linux/arm64`) and confirmed matplotlib,
numpy, scipy, scikit-learn, statsmodels, and pillow all show as `Downloaded` in the
`uv sync` log, never `Building` — the apt-get block installed 50 packages that `uv sync`
never touched. `linux/amd64` coverage comes from this PR's own CI: `pr-checks.yml`'s
`docker-build` job builds `bloommcp/Dockerfile` on `ubuntu-latest` (amd64) on every PR,
and `compose-health-check` + `dev-stack-smoke` (which calls a real matplotlib-rendering
MCP tool, `sleap_roots_plot_trait_histograms`, end-to-end) both run against that same
build — so both architectures are validated before merge without adding a new CI job.

**Accepted risk: no pixel-level rendering comparison.** Neither before nor after this
change does the test suite do pixel/hash/snapshot comparison on generated plot PNGs
(`test_viz_tools.py` and `live_plot_tool_smoke.py` only assert `.is_file()`). This change
can't introduce a rendering regression either way, since matplotlib's manylinux wheels
statically vendor their own FreeType/libpng regardless of wheel-vs-source — but the gap
itself is a pre-existing, explicitly accepted risk rather than one this proposal closes.

## What Changes

- Remove the `apt-get` block from `bloommcp/Dockerfile` (the `RUN apt-get update && ...`
  step and its preceding comment) — no replacement system packages are needed.
- Add `tests/unit/test_bloommcp_dockerfile_shape.py`, a small shape guard (mirroring the
  existing `test_bloomcli_dockerfile_shape.py` convention) that fails CI if a future PR
  reintroduces `apt-get install` in `bloommcp/Dockerfile` — the `bloommcp-packaging`
  spec requirement below is machine-enforced, not just documentation.
- No dependency version, application code, or runtime-behavior change — Dockerfile and
  a new test only.

## Impact

- Affected specs: `bloommcp-packaging` (**ADDED** — codifies that the runtime image
  ships without a native build toolchain).
- Affected code: `bloommcp/Dockerfile`, `tests/unit/test_bloommcp_dockerfile_shape.py`
  (new).
- Risk: if a future dependency bump (of matplotlib/numpy/scipy or a new native
  dependency) drops wheel coverage for a pinned platform, the Dockerfile would need an
  `apt-get` block reintroduced — the same way any other Bloom service's Dockerfile
  would when it first needs one. `uv sync --frozen` fails outright on a missing wheel
  (there's no compiler to silently fall back to), so this regression surfaces as a
  build failure in `pr-checks.yml`'s `docker-build` job, not a silent behavior change —
  and the new shape-guard test would need updating in the same PR that reintroduces the
  block, which is the intended, visible way for that to happen.
