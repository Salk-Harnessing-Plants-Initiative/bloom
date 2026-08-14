## 1. Confirm the apt-get block is dead weight

- [x] 1.1 Build the current `bloommcp/Dockerfile` with `--no-cache` (locally, on
      `linux/arm64`) and capture the log; confirm `uv sync --frozen --no-dev --no-cache`
      resolves wheels — not just for matplotlib/numpy/scipy, but for every native-code
      package in the dependency closure (scikit-learn, statsmodels, pillow, contourpy,
      kiwisolver, fonttools, numba, llvmlite) — rather than invoking `gcc`/compiling any
      of them from source. `linux/amd64` is covered by this PR's own CI `docker-build`
      job (see proposal.md), so no separate manual amd64 build is needed.

## 2. Implementation

- [x] 2.1 Remove the `apt-get` block (and its inline comment) from `bloommcp/Dockerfile`.
- [x] 2.2 Add `tests/unit/test_bloommcp_dockerfile_shape.py` asserting
      `"apt-get install" not in Dockerfile text`, mirroring
      `tests/unit/test_bloomcli_dockerfile_shape.py`'s `test_no_apt_get_install` — the
      regression guard that makes the new `bloommcp-packaging` requirement
      machine-enforced rather than just documented.
- [x] 2.3 Rebuild the image with `--no-cache` against the trimmed Dockerfile; confirm
      `uv sync` still succeeds via wheels with no compiler present in the base image.
- [x] 2.4 Run the trimmed image and confirm `python -m bloom_mcp` boots and `/health`
      returns OK, matching pre-change behavior.

## 3. Validation

- [x] 3.1 Run the `bloommcp` test suite (`uv run --extra test pytest`), including the
      new shape-guard test, to confirm no regression from the Dockerfile-only change.
- [x] 3.2 `openspec validate remove-bloommcp-unused-apt-deps --strict` passes.

## 4. Wrap up

- [ ] 4.1 Open a PR referencing issue #590.
- [ ] 4.2 After merge/deploy, archive this change (`openspec archive
      remove-bloommcp-unused-apt-deps --skip-specs --yes` is not applicable here since
      this change does add a spec — use the normal archive flow, updating
      `bloommcp-packaging`'s spec).
