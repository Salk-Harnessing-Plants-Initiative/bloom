"""scrna-analysis <step> --root /shared/runs/<run> [--publish s3://<bucket>/runs_output/<run>] [options]"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .steps import StepError
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scrna-analysis")
    parser.add_argument("step", choices=["preprocess", "cluster", "build-h5ad"])
    parser.add_argument(
        "--root", required=True, help="the run's working folder, e.g. /shared/runs/42"
    )
    parser.add_argument(
        "--publish",
        help="where build-h5ad copies the final file, e.g. s3://bloomv2-workflows/runs_output/42",
    )
    parser.add_argument("--sample", help="the sample name, for build-h5ad's file name")
    parser.add_argument(
        "--n-top-genes",
        type=int,
        default=None,
        help="variable genes for the PCA (preprocess)",
    )
    args = parser.parse_args(argv)

    store = Store(args.root)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        try:
            if args.step == "preprocess":
                from . import preprocess

                n = args.n_top_genes or preprocess.DEFAULT_N_TOP_GENES
                preprocess.run(store, workdir, n_top_genes=n)
            elif args.step == "cluster":
                from . import cluster

                cluster.run(store, workdir)
            else:
                from . import build

                if not args.sample:
                    parser.error("build-h5ad needs --sample")
                publish = Store(args.publish) if args.publish else None
                build.run(store, workdir, sample=args.sample, publish=publish)
        except StepError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return error.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
