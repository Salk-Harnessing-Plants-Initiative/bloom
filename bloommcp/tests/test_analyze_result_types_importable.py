"""The serializable analyze result types resolve from the released package.

#327 raised the `sleap-roots-analyze` floor to `>=0.1.0a3` specifically so bloom-mcp
Tiers 3/4 (#308/#309) can import the typed `perform_*` results from the **released**
package instead of writing a dict->type adapter against `0.1.0a2`. This guard fails if
the installed analyze drops back below a3 (or stops exporting the types), so the floor's
purpose is verified by a test rather than only asserted in the spec.
"""

from __future__ import annotations


def test_serializable_result_types_import_from_sleap_roots_analyze():
    from sleap_roots_analyze import (  # noqa: F401
        GMMResult,
        HeritabilityResult,
        KMeansResult,
        PCAResult,
    )
