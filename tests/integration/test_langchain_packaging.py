"""langchain-agent packaging hygiene — issue #475.

Guards against re-introducing either problem fixed by the
remove-langchain-unused-sleap-out-csv change:
  - langchain/SLEAP_OUT_CSV/ was 6.6MB of dead vendored data baked into every
    langchain-agent Docker image (nothing in langchain/ read it).
  - router.py / context_tools.py named illustrative CSV filenames that only
    ever existed in that now-deleted data.
"""
import os
import sys
import tempfile
from pathlib import Path

_LANGCHAIN_DIR = Path(__file__).resolve().parents[2] / "langchain"
if str(_LANGCHAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_LANGCHAIN_DIR))

# tools/__init__.py transitively imports base.py, which reads SUPABASE_URL /
# BLOOM_AGENT_KEY at module level — provide safe defaults (mirrors
# test_context_loader.py / test_top_router.py in this same directory).
_TMP = tempfile.mkdtemp(prefix="langchain_packaging_test_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid")
os.environ.setdefault("FRONTEND_URL", "http://test.invalid")
os.environ.setdefault("SUPABASE_URL", "http://test.invalid")
os.environ.setdefault("BLOOM_AGENT_KEY", "test-token-not-real")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key-not-real")
os.environ.setdefault("NEXT_PUBLIC_APP_URL", "http://test.invalid")

from prompts.router import ROUTER_FEW_SHOTS  # noqa: E402
from tools.context_tools import CONTEXT_MCP  # noqa: E402

_STALE_FILENAMES = (
    "cylinder_alfalfa_gwas_wave2",
    "cylinder_amaranth_tis108_exp1",
    "turface_rice_treatment_exp1",
)


def test_no_sleap_out_csv_directory():
    assert not (_LANGCHAIN_DIR / "SLEAP_OUT_CSV").exists()


def test_router_few_shots_reference_no_deleted_filenames():
    text = " ".join(example for example, _bucket in ROUTER_FEW_SHOTS)
    for stale in _STALE_FILENAMES:
        assert stale not in text


def test_context_mcp_references_no_deleted_filenames():
    for stale in _STALE_FILENAMES:
        assert stale not in CONTEXT_MCP
