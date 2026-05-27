"""
Cylinder phenotyping tools for querying experiments, plants, scans, and traits.
"""
import statistics
from typing import Optional
import httpx
from langchain_core.tools import tool
from .base import REST_URL, get_headers
from .cyl_viz_tools import _accession_boxplot, _accession_ranked_profile, _wave_boxplot
from helpers.plot_renderer import render_and_save
from helpers.trait_name_resolver import _resolve_trait_name


_EXPERIMENT_CHIPS_LIMIT = 20

_BASELINE_EXPERIMENT_ACTIONS = [
    {"label": "List the traits", "prompt": "List the available traits"},
    {"label": "Show trait statistics", "prompt": "Show me statistics for a trait"},
    {"label": "Compare across waves", "prompt": "Compare a trait across waves"},
]


@tool
def list_experiments_tool(limit: int = 50) -> dict:
    """List all cylinder phenotyping experiments with species info.

    Each experiment dict also includes `trait_measurement_count` (total
    scan-trait rows summed across all waves of that experiment) and
    `distinct_traits_count` (number of distinct trait names measured).
    LLM guidance: when the user wants trait analysis, prefer experiments
    with `trait_measurement_count > 0`. For experiments with zero
    measurements, narrate that no trait data has been computed yet rather
    than calling an analysis tool against them.
    """
    response = httpx.get(
        f"{REST_URL}/cyl_experiments",
        headers=get_headers(),
        params={
            "select": "id,name,created_at,species(id,common_name),people(id,name)",
            "limit": limit
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list experiments: {response.text}")
    experiments = response.json()

    # Fetch aggregate counts per experiment from the view in one call.
    # PostgREST can't GROUP BY directly, so we pull rows and aggregate in Python.
    counts_response = httpx.get(
        f"{REST_URL}/cyl_trait_by_experiment_wave",
        headers=get_headers(),
        params={"select": "experiment_id,trait_name,n"},
    )
    counts_by_exp: dict[int, dict] = {}
    if counts_response.status_code == 200:
        for row in counts_response.json():
            exp_id = row.get("experiment_id")
            if exp_id is None:
                continue
            bucket = counts_by_exp.setdefault(
                exp_id, {"total_n": 0, "traits": set()}
            )
            bucket["total_n"] += row.get("n") or 0
            if row.get("trait_name"):
                bucket["traits"].add(row["trait_name"])

    for exp in experiments:
        bucket = counts_by_exp.get(exp.get("id"))
        exp["trait_measurement_count"] = bucket["total_n"] if bucket else 0
        exp["distinct_traits_count"] = len(bucket["traits"]) if bucket else 0

    experiment_chips = [
        {"label": exp["name"], "prompt": f"Show waves for {exp['name']}"}
        for exp in experiments[:_EXPERIMENT_CHIPS_LIMIT]
        if exp.get("name")
    ]
    return {
        "count": len(experiments),
        "experiments": experiments,
        "followup_actions": experiment_chips + _BASELINE_EXPERIMENT_ACTIONS,
    }


@tool
def get_experiment_by_id_tool(experiment_id: int) -> dict:
    """Get a cylinder experiment by ID with full details."""
    response = httpx.get(
        f"{REST_URL}/cyl_experiments",
        headers=get_headers(),
        params={
            "id": f"eq.{experiment_id}",
            "select": "id,name,created_at,species(id,common_name,genus,species),people(id,name,email)"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get experiment: {response.text}")
    data = response.json()
    return data[0] if data else {}


@tool
def list_waves_by_experiment_tool(experiment_id: int) -> list:
    """List all planting waves for a given experiment."""
    response = httpx.get(
        f"{REST_URL}/cyl_waves",
        headers=get_headers(),
        params={
            "experiment_id": f"eq.{experiment_id}",
            "select": "id,name,number"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list waves: {response.text}")
    return response.json()


@tool
def list_plants_tool(experiment_id: Optional[int] = None, wave_id: Optional[int] = None, limit: int = 100) -> list:
    """List plants with optional experiment or wave filter."""
    params = {
        "select": "id,qr_code,accessions(id,name),cyl_experiments(id,name),cyl_waves(id,name)",
        "limit": limit
    }
    if experiment_id is not None:
        params["experiment_id"] = f"eq.{experiment_id}"
    if wave_id is not None:
        params["wave_id"] = f"eq.{wave_id}"

    response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params=params
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list plants: {response.text}")
    return response.json()


@tool
def get_plant_by_qr_tool(qr_code: str) -> dict:
    """Get a plant by its QR code."""
    response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params={
            "qr_code": f"eq.{qr_code}",
            "select": "id,qr_code,accessions(id,name,species(common_name)),cyl_experiments(id,name),cyl_waves(id,name,number)"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get plant: {response.text}")
    data = response.json()
    return data[0] if data else {}


@tool
def list_scans_tool(limit: int = 50, plant_id: Optional[int] = None) -> list:
    """List cylinder plant scans with optional plant_id filter."""
    params = {"select": "*", "limit": limit}
    if plant_id is not None:
        params["plant_id"] = f"eq.{plant_id}"

    response = httpx.get(
        f"{REST_URL}/cyl_scans",
        headers=get_headers(),
        params=params
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list scans: {response.text}")
    return response.json()


@tool
def get_scan_tool(scan_id: int) -> dict:
    """Get a cylinder scan by ID with its images."""
    response = httpx.get(
        f"{REST_URL}/cyl_scans",
        headers=get_headers(),
        params={
            "id": f"eq.{scan_id}",
            "select": "*,cyl_images(*)"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get scan: {response.text}")
    data = response.json()
    return data[0] if data else {}


@tool
def get_scan_traits_tool(scan_id: int) -> list:
    """Get all measured traits for a specific scan."""
    response = httpx.get(
        f"{REST_URL}/cyl_scan_traits",
        headers=get_headers(),
        params={
            "scan_id": f"eq.{scan_id}",
            "select": "id,trait_name,value,source_id"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get scan traits: {response.text}")
    return response.json()


@tool
def list_scanners_tool() -> list:
    """List all scanner devices."""
    response = httpx.get(
        f"{REST_URL}/cyl_scanners",
        headers=get_headers(),
        params={"select": "id,name,location"}
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list scanners: {response.text}")
    return response.json()


@tool
def list_phenotypers_tool(limit: int = 50) -> list:
    """List all phenotypers (imaging devices)."""
    response = httpx.get(
        f"{REST_URL}/phenotypers",
        headers=get_headers(),
        params={"select": "*", "limit": limit}
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list phenotypers: {response.text}")
    return response.json()


@tool
def get_plant_scan_history_tool(plant_id: int) -> list:
    """Get all scans for a plant with traits and image counts."""
    response = httpx.get(
        f"{REST_URL}/cyl_scans",
        headers=get_headers(),
        params={
            "plant_id": f"eq.{plant_id}",
            "select": "id,date_scanned,uploaded_at,cyl_scanners(name),cyl_images(id),cyl_scan_traits(trait_name,value)",
            "order": "date_scanned.desc"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get plant scan history: {response.text}")
    return response.json()

########################## Analytics Tools ###########################


@tool
def get_plant_growth_timeline_tool(qr_code: str) -> dict:
    """
    Get the growth timeline for a plant showing how traits change over time.

    Args:
        qr_code: The plant's QR code identifier (e.g., 'SOY-W1-001')

    Returns:
        Dictionary with plant info and list of scans with trait measurements,
        ordered chronologically to show growth progression.
    """
    # First, get the plant info by QR code
    plant_response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params={
            "qr_code": f"eq.{qr_code}",
            "select": "id,qr_code,germ_day,created_at,accessions(id,name),cyl_waves(id,name)"
        }
    )
    if plant_response.status_code != 200:
        raise Exception(f"Failed to get plant: {plant_response.text}")

    plants = plant_response.json()
    if not plants:
        return {"error": f"No plant found with QR code '{qr_code}'"}

    plant = plants[0]
    plant_id = plant["id"]

    # Get all scans for this plant with traits
    scans_response = httpx.get(
        f"{REST_URL}/cyl_scans",
        headers=get_headers(),
        params={
            "plant_id": f"eq.{plant_id}",
            "select": "id,date_scanned,plant_age_days,cyl_scan_traits(value,cyl_traits(name))",
            "order": "date_scanned.asc"
        }
    )
    if scans_response.status_code != 200:
        raise Exception(f"Failed to get scans: {scans_response.text}")

    scans = scans_response.json()

    # Transform the data into a cleaner format
    timeline = []
    for scan in scans:
        # Convert nested trait data to a simple dict
        traits = {}
        for trait_record in scan.get("cyl_scan_traits", []):
            trait_name = trait_record.get("cyl_traits", {}).get("name")
            if trait_name:
                traits[trait_name] = trait_record.get("value")

        timeline.append({
            "scan_id": scan["id"],
            "date_scanned": scan["date_scanned"],
            "plant_age_days": scan.get("plant_age_days"),
            "traits": traits
        })

    return {
        "plant_id": plant_id,
        "qr_code": plant["qr_code"],
        "accession": plant.get("accessions", {}).get("name"),
        "wave": plant.get("cyl_waves", {}).get("name"),
        "germ_day": plant.get("germ_day"),
        "scan_count": len(timeline),
        "timeline": timeline
    }


@tool
def get_plants_by_accession_tool(accession_name: str, experiment_id: Optional[int] = None) -> dict:
    """
    Find all plants of a specific accession (plant variety/genotype).
    Use this to discover plant QR codes before querying growth data.

    Args:
        accession_name: The accession/variety name (e.g., 'Williams-82', 'PI-416937')
                       Supports partial matching (case-insensitive)
        experiment_id: Optional - filter to a specific experiment

    Returns:
        Dictionary with accession info and list of plants with their QR codes,
        wave info, and scan counts.
    """
    # First, find the accession(s) matching the name
    accession_response = httpx.get(
        f"{REST_URL}/accessions",
        headers=get_headers(),
        params={
            "name": f"ilike.*{accession_name}*",
            "select": "id,name"
        }
    )
    if accession_response.status_code != 200:
        raise Exception(f"Failed to search accessions: {accession_response.text}")

    accessions = accession_response.json()
    if not accessions:
        return {"error": f"No accession found matching '{accession_name}'"}

    # Get plants for all matching accessions
    accession_ids = [a["id"] for a in accessions]

    # Build query params
    params = {
        "accession_id": f"in.({','.join(map(str, accession_ids))})",
        "select": "id,qr_code,germ_day,created_at,accessions(name),cyl_waves(id,name),cyl_scans(id)",
        "order": "qr_code.asc"
    }
    if experiment_id is not None:
        # Need to filter through wave -> experiment relationship
        params["cyl_waves.experiment_id"] = f"eq.{experiment_id}"

    plants_response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params=params
    )
    if plants_response.status_code != 200:
        raise Exception(f"Failed to get plants: {plants_response.text}")

    plants = plants_response.json()

    # Transform to cleaner format
    plant_list = []
    for plant in plants:
        scan_count = len(plant.get("cyl_scans", []) or [])
        plant_list.append({
            "plant_id": plant["id"],
            "qr_code": plant["qr_code"],
            "accession": plant.get("accessions", {}).get("name"),
            "wave": plant.get("cyl_waves", {}).get("name"),
            "germ_day": plant.get("germ_day"),
            "scan_count": scan_count
        })

    return {
        "accessions_matched": [a["name"] for a in accessions],
        "plant_count": len(plant_list),
        "plants": plant_list,
        "hint": "Use get_plant_growth_timeline_tool(qr_code) to see detailed growth data for a specific plant"
    }


@tool
def list_accessions_tool(limit: int = 50) -> list:
    """
    List all available accessions (plant varieties/genotypes).
    Use this to discover what accessions exist before searching for plants.

    Returns:
        List of accession names available in the database.
    """
    response = httpx.get(
        f"{REST_URL}/accessions",
        headers=get_headers(),
        params={
            "select": "id,name",
            "order": "name.asc",
            "limit": limit
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list accessions: {response.text}")

    accessions = response.json()
    return {
        "count": len(accessions),
        "accessions": [a["name"] for a in accessions],
        "hint": "Use get_plants_by_accession_tool(accession_name) to find plants of a specific variety"
    }


_TRAIT_CHIPS_LIMIT = 20


@tool
def list_traits_tool(
    experiment_id: Optional[int] = None,
    wave_ids: Optional[list[int]] = None,
) -> dict:
    """
    List available phenotype traits.

    Default (no params): returns the global trait registry from `cyl_traits`.
    With `experiment_id` or `wave_ids` set: returns ONLY trait names actually
    measured in that scope (read from `cyl_trait_by_experiment_wave`). This is
    what callers should use when the user is exploring a specific experiment
    or wave — the chips then surface exactly what the analysis tools can
    compute against.

    When called with a scope, the `followup_actions` list also includes a
    "Type a different trait" chip at the end, giving the user a discoverable
    path back to the global list. Alongside this chip, ALWAYS narrate to the
    user that they can type any other trait name in the chat input directly.

    Args:
        experiment_id: Optional. Filter traits to those measured in this
            experiment. Mutually exclusive with wave_ids.
        wave_ids: Optional. Filter traits to those measured in any of these
            waves. Mutually exclusive with experiment_id.

    Returns:
        {count, traits, followup_actions, hint, scope}
    """
    if experiment_id is not None and wave_ids is not None:
        raise ValueError(
            "list_traits_tool: experiment_id and wave_ids are mutually "
            "exclusive. Pass one or neither, not both."
        )

    scoped = experiment_id is not None or wave_ids is not None

    if scoped:
        # Pull distinct trait names from the view, filtered to scope
        params: dict[str, str] = {"select": "trait_name"}
        if experiment_id is not None:
            params["experiment_id"] = f"eq.{experiment_id}"
        else:
            ids_csv = ",".join(str(w) for w in wave_ids or [])
            params["wave_id"] = f"in.({ids_csv})"
        view_response = httpx.get(
            f"{REST_URL}/cyl_trait_by_experiment_wave",
            headers=get_headers(),
            params=params,
        )
        if view_response.status_code != 200:
            raise Exception(f"Failed to list scoped traits: {view_response.text}")
        names = sorted({row["trait_name"] for row in view_response.json() if row.get("trait_name")})
    else:
        # Global trait registry — current default behavior
        registry_response = httpx.get(
            f"{REST_URL}/cyl_traits",
            headers=get_headers(),
            params={"select": "id,name", "order": "name.asc"},
        )
        if registry_response.status_code != 200:
            raise Exception(f"Failed to list traits: {registry_response.text}")
        names = [t["name"] for t in registry_response.json()]

    followup_actions: list[dict] = [
        {"label": name, "prompt": f"Show me stats for {name}"}
        for name in names[:_TRAIT_CHIPS_LIMIT]
    ]
    if scoped:
        # Soft-other chip — discoverability nudge back to global list
        followup_actions.append(
            {"label": "Type a different trait", "prompt": "Show me all available traits"}
        )

    return {
        "count": len(names),
        "traits": names,
        "followup_actions": followup_actions,
        "scope": {"experiment_id": experiment_id, "wave_ids": wave_ids},
        "hint": (
            "These are the traits measured in scope. Click a chip to drill in, "
            "or type a different trait name in the chat."
            if scoped else
            "Global trait registry. Pass experiment_id or wave_ids to scope the list to actually-measured traits."
        ),
    }


_ACCESSION_BOXPLOT_N_THRESHOLD = 10


@tool
def compare_accessions_in_wave_tool(
    trait_name: str,
    wave_id: int,
    plant_age_days: Optional[int] = None,
) -> dict:
    """Compare a trait across all accessions within ONE wave.

    Single-wave scope is deliberate: within a wave, all plants share the
    same planting date and growing conditions, so accession-to-accession
    differences reflect genotype rather than wave-level confounds.

    AGE HANDLING (the key knob): within a wave, plants are typically scanned
    at multiple timepoints (e.g. days 7, 14, 21, 28, 35). Older plants are
    bigger, so mixing all ages in one accession's stats would confound
    genotype with age. Two modes:

      - `plant_age_days` UNSET (default): "latest scan per plant" — for each
        plant the tool uses ONLY the scan with the highest plant_age_days.
        Each plant contributes one value. Best for: final-readout comparisons.

      - `plant_age_days` SET: only scans at that exact age contribute.
        Best for: comparing accessions at a specific developmental stage.

    If the user's intent is ambiguous, ASK them: "do you want the comparison
    at a specific plant age (e.g. day 21), or summarized across each plant's
    final scan?" Then pass `plant_age_days` accordingly.

    For each accession with at least one trait value under the chosen mode,
    returns descriptive stats (n, mean, std, median, min, max) ranked by
    median desc. Renders a chart:
      - n_accessions <= 10  → side-by-side boxplot (one box per accession)
      - n_accessions  > 10  → ranked profile (dot + Q1-to-Q3 error bar per
        accession; top-3 and bottom-3 inline-labeled)

    For large panels (n > 10) a `summary` block is also returned with the
    median-of-medians, range, top-3 and bottom-3 accession names.

    Args:
        trait_name: The trait to compare. Typos return a suggestions payload.
        wave_id: The wave to scope the comparison to.
        plant_age_days: Optional. If set, only scans at this exact age
            contribute. If None (default), uses the latest scan per plant.

    Returns:
        Success: {trait_name, scope, n_accessions, rankings, plot_url,
                  plot_layout, [summary if n>10]}
        Trait miss: {error, trait_name, suggestions, sample_traits}
        No data: {trait_name, scope, n_accessions: 0, rankings: [], note}

    Reporting guidance for the LLM (chat renders bot messages as markdown):
      - Format `rankings` as a markdown table with columns: rank, accession,
        n, mean, std, median, min, max
      - Bold (**…**) the rank-1 row and the rank-N row so the leaders and
        laggards are visible at a glance
      - Italicize (_…_) any caveats — e.g. small `n`, ties, or the scan_mode
        assumption ("computed from each plant's latest scan")
      - Always describe the rendered chart in one sentence so users who
        can't see images get the same signal
      - State which scan_mode was used ("latest scan per plant" or "at age
        N days") so users see the assumption
    """
    # 1. Get distinct trait names actually measured in scope (for fuzzy match candidates)
    candidates_response = httpx.get(
        f"{REST_URL}/cyl_trait_by_experiment_wave",
        headers=get_headers(),
        params={"wave_id": f"eq.{wave_id}", "select": "trait_name"},
    )
    if candidates_response.status_code != 200:
        raise Exception(f"Failed to fetch trait candidates: {candidates_response.text}")
    candidates = sorted({row["trait_name"] for row in candidates_response.json()})

    # 2. Resolve trait name (exact match → continue; otherwise → suggestion payload)
    resolved = _resolve_trait_name(trait_name, candidates)
    if not resolved["matched"]:
        return {
            "error": "trait not found",
            "trait_name": trait_name,
            "suggestions": resolved["suggestions"],
            "sample_traits": resolved["sample_traits"],
        }
    canonical_name = resolved["name"]

    # 3. Resolve trait_id from canonical name
    trait_response = httpx.get(
        f"{REST_URL}/cyl_traits",
        headers=get_headers(),
        params={"name": f"eq.{canonical_name}", "select": "id"},
    )
    if trait_response.status_code != 200:
        raise Exception(f"Failed to fetch trait id: {trait_response.text}")
    trait_rows = trait_response.json()
    if not trait_rows:
        return {
            "trait_name": canonical_name,
            "scope": {"wave_id": wave_id},
            "n_accessions": 0,
            "rankings": [],
            "note": "trait resolved against view but no row in cyl_traits registry",
        }
    trait_id = trait_rows[0]["id"]

    # 4. Fetch raw trait values per plant, scoped to the wave
    plants_response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params={
            "wave_id": f"eq.{wave_id}",
            "select": "id,accessions(name),cyl_scans(plant_age_days,cyl_scan_traits(value,trait_id))",
        },
    )
    if plants_response.status_code != 200:
        raise Exception(f"Failed to fetch trait values: {plants_response.text}")
    plants = plants_response.json()

    # 5. Group values by accession, honoring the age-handling mode
    scan_mode = "specific_age" if plant_age_days is not None else "latest_per_plant"
    scope = {"wave_id": wave_id, "scan_mode": scan_mode, "plant_age_days": plant_age_days}

    values_by_accession: dict[str, list[float]] = {}
    for plant in plants:
        accession_name = (plant.get("accessions") or {}).get("name")
        if not accession_name:
            continue
        scans = plant.get("cyl_scans") or []
        if not scans:
            continue

        if plant_age_days is not None:
            # Specific-age mode: keep only scans at this exact age
            matching_scans = [s for s in scans if s.get("plant_age_days") == plant_age_days]
        else:
            # Latest-per-plant mode: keep only the scan with the highest age.
            # Scans without a plant_age_days value are skipped (we can't rank them).
            aged_scans = [s for s in scans if s.get("plant_age_days") is not None]
            if not aged_scans:
                continue
            latest_age = max(s["plant_age_days"] for s in aged_scans)
            matching_scans = [s for s in aged_scans if s["plant_age_days"] == latest_age]

        for scan in matching_scans:
            for trait_record in scan.get("cyl_scan_traits") or []:
                if (
                    trait_record.get("trait_id") == trait_id
                    and trait_record.get("value") is not None
                ):
                    values_by_accession.setdefault(accession_name, []).append(
                        float(trait_record["value"])
                    )

    # 6. Empty-result branch — no chart rendered
    if not values_by_accession:
        note_suffix = (
            f" at plant_age_days={plant_age_days}"
            if plant_age_days is not None else
            " (latest scan per plant)"
        )
        return {
            "trait_name": canonical_name,
            "scope": scope,
            "n_accessions": 0,
            "rankings": [],
            "note": f"no scans for this trait in the requested wave{note_suffix}",
        }

    # 7. Compute per-accession stats
    rankings: list[dict] = []
    for accession_name, values in values_by_accession.items():
        rankings.append({
            "accession_name": accession_name,
            "n": len(values),
            "mean": round(statistics.mean(values), 2),
            "std": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
            "median": round(statistics.median(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
        })

    # 8. Sort by median desc + assign rank
    rankings.sort(key=lambda r: r["median"], reverse=True)
    for i, r in enumerate(rankings, start=1):
        r["rank"] = i

    n_accessions = len(rankings)

    # 9. Render chart — pick layout by N
    if n_accessions <= _ACCESSION_BOXPLOT_N_THRESHOLD:
        fig = _accession_boxplot(rankings, values_by_accession, canonical_name)
        plot_layout = "boxplot"
    else:
        fig = _accession_ranked_profile(rankings, values_by_accession, canonical_name)
        plot_layout = "ranked_profile"

    plot_url = render_and_save(fig, prefix="accession_rank", namespace="cyl_supabase")

    result: dict = {
        "trait_name": canonical_name,
        "scope": scope,
        "n_accessions": n_accessions,
        "rankings": rankings,
        "plot_url": plot_url,
        "plot_layout": plot_layout,
    }

    # 10. Summary block for large panels (N > 10)
    if n_accessions > _ACCESSION_BOXPLOT_N_THRESHOLD:
        medians = [r["median"] for r in rankings]
        result["summary"] = {
            "median_of_accession_medians": round(statistics.median(medians), 2),
            "range_of_medians": [round(min(medians), 2), round(max(medians), 2)],
            "top_3": [r["accession_name"] for r in rankings[:3]],
            "bottom_3": [r["accession_name"] for r in rankings[-3:]],
        }

    return result


@tool
def compare_waves_for_accession_tool(
    trait_name: str,
    accession_name: str,
    experiment_id: int,
    plant_age_days: Optional[int] = None,
) -> dict:
    """Within ONE accession, compare a trait's distribution across the waves
    of one experiment.

    Reveals wave-to-wave consistency (or wave effects) for this accession —
    e.g. "is indi-12 stable across waves of alfalfa-2024, or does wave 3
    look different?"

    REQUIRES the accession to span every wave of the experiment. Consistency
    only makes sense when the comparison is apples-to-apples. If the
    accession is missing from any wave, the tool returns an error pointing
    the caller to `compare_accessions_in_wave_tool` for per-wave analysis.

    Age mode (same convention as compare_accessions_in_wave_tool):
      - `plant_age_days` UNSET (default): latest scan per plant.
      - `plant_age_days` SET: only scans at that exact age contribute.

    Each `per_wave` entry also reports its scan-age context
    (`plant_age_days_distinct`, `_min`, `_max`) so the LLM can flag waves
    that mix ages (which would muddy the wave-to-wave comparison).

    Returns:
        Success: {trait_name, accession_name, experiment_id, n_waves,
                  per_wave, consistency, scope, plot_url, plot_layout}
        Coverage miss: {error, experiment_waves, accession_present_in_waves,
                  missing_waves, note}
        Accession not found: {error, accession_name, experiment_id,
                  available_accessions_sample}
        Trait miss: {error, trait_name, suggestions, sample_traits}

    Reporting guidance for the LLM (chat renders bot messages as markdown):
      - Format `per_wave` as a markdown table with columns: wave, n, mean,
        std, median, min, max, age range (use the
        `plant_age_days_min/max` fields)
      - Italicize (_…_) a caveat sentence for any wave where
        `plant_age_days_min != plant_age_days_max` — that wave mixes ages and
        the box is harder to interpret
      - Bold (**…**) the `consistency.cv_of_wave_medians` value and translate
        it: CV < 0.1 = highly consistent; 0.1–0.3 = moderate; > 0.3 =
        wave-dependent. State which scan_mode produced the numbers
      - Describe the rendered chart in one sentence
    """
    # 1. Fetch experiment waves
    waves_response = httpx.get(
        f"{REST_URL}/cyl_waves",
        headers=get_headers(),
        params={
            "experiment_id": f"eq.{experiment_id}",
            "select": "id,number,name",
            "order": "number.asc",
        },
    )
    if waves_response.status_code != 200:
        raise Exception(f"Failed to fetch experiment waves: {waves_response.text}")
    experiment_waves = waves_response.json()
    if not experiment_waves:
        return {
            "error": "experiment has no waves",
            "experiment_id": experiment_id,
        }
    experiment_wave_ids = sorted(w["id"] for w in experiment_waves)
    wave_meta = {w["id"]: w for w in experiment_waves}

    # 2. Get trait name candidates from view (scoped to this experiment)
    candidates_response = httpx.get(
        f"{REST_URL}/cyl_trait_by_experiment_wave",
        headers=get_headers(),
        params={"experiment_id": f"eq.{experiment_id}", "select": "trait_name"},
    )
    if candidates_response.status_code != 200:
        raise Exception(f"Failed to fetch trait candidates: {candidates_response.text}")
    candidates = sorted({row["trait_name"] for row in candidates_response.json()})

    resolved = _resolve_trait_name(trait_name, candidates)
    if not resolved["matched"]:
        return {
            "error": "trait not found",
            "trait_name": trait_name,
            "suggestions": resolved["suggestions"],
            "sample_traits": resolved["sample_traits"],
        }
    canonical_name = resolved["name"]

    # 3. Resolve trait_id
    trait_response = httpx.get(
        f"{REST_URL}/cyl_traits",
        headers=get_headers(),
        params={"name": f"eq.{canonical_name}", "select": "id"},
    )
    if trait_response.status_code != 200:
        raise Exception(f"Failed to fetch trait id: {trait_response.text}")
    trait_rows = trait_response.json()
    if not trait_rows:
        return {
            "error": "trait resolved against view but no row in cyl_traits registry",
            "trait_name": canonical_name,
        }
    trait_id = trait_rows[0]["id"]

    # 4. Resolve accession_id
    accession_response = httpx.get(
        f"{REST_URL}/accessions",
        headers=get_headers(),
        params={"name": f"eq.{accession_name}", "select": "id"},
    )
    if accession_response.status_code != 200:
        raise Exception(f"Failed to resolve accession: {accession_response.text}")
    accession_rows = accession_response.json()
    if not accession_rows:
        # Surface a sample of accessions in this experiment so the LLM can hint
        sample_resp = httpx.get(
            f"{REST_URL}/cyl_plants",
            headers=get_headers(),
            params={
                "wave_id": f"in.({','.join(str(w) for w in experiment_wave_ids)})",
                "select": "accessions(name)",
                "limit": 100,
            },
        )
        sample = []
        if sample_resp.status_code == 200:
            sample = sorted({
                (r.get("accessions") or {}).get("name")
                for r in sample_resp.json()
                if r.get("accessions")
            })[:10]
        return {
            "error": "accession not found in experiment",
            "accession_name": accession_name,
            "experiment_id": experiment_id,
            "available_accessions_sample": sample,
        }
    accession_id = accession_rows[0]["id"]

    # 5. Fetch plants of this accession in this experiment's waves
    ids_csv = ",".join(str(w) for w in experiment_wave_ids)
    plants_response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params={
            "accession_id": f"eq.{accession_id}",
            "wave_id": f"in.({ids_csv})",
            "select": "id,wave_id,cyl_scans(plant_age_days,cyl_scan_traits(value,trait_id))",
        },
    )
    if plants_response.status_code != 200:
        raise Exception(f"Failed to fetch plants: {plants_response.text}")
    plants = plants_response.json()

    # 6. Coverage check — accession must appear in EVERY wave of the experiment
    accession_present_in_waves = sorted({p["wave_id"] for p in plants if p.get("wave_id")})
    missing_waves = [w for w in experiment_wave_ids if w not in accession_present_in_waves]
    if missing_waves:
        return {
            "error": "accession does not span all waves of the experiment",
            "accession_name": accession_name,
            "experiment_id": experiment_id,
            "experiment_waves": experiment_wave_ids,
            "accession_present_in_waves": accession_present_in_waves,
            "missing_waves": missing_waves,
            "note": (
                "Consistency analysis requires the accession to appear in every wave. "
                "Use compare_accessions_in_wave_tool to analyze each wave separately, "
                "or pick an accession with full coverage."
            ),
        }

    # 7. Group values by wave, honoring age mode
    scan_mode = "specific_age" if plant_age_days is not None else "latest_per_plant"
    values_by_wave: dict[int, list[float]] = {}
    ages_by_wave: dict[int, set[int]] = {}
    for plant in plants:
        wave_id = plant.get("wave_id")
        if wave_id is None:
            continue
        scans = plant.get("cyl_scans") or []
        if not scans:
            continue

        if plant_age_days is not None:
            matching_scans = [s for s in scans if s.get("plant_age_days") == plant_age_days]
        else:
            aged_scans = [s for s in scans if s.get("plant_age_days") is not None]
            if not aged_scans:
                continue
            latest_age = max(s["plant_age_days"] for s in aged_scans)
            matching_scans = [s for s in aged_scans if s["plant_age_days"] == latest_age]

        for scan in matching_scans:
            for trait_record in scan.get("cyl_scan_traits") or []:
                if (
                    trait_record.get("trait_id") == trait_id
                    and trait_record.get("value") is not None
                ):
                    values_by_wave.setdefault(wave_id, []).append(
                        float(trait_record["value"])
                    )
                    age = scan.get("plant_age_days")
                    if age is not None:
                        ages_by_wave.setdefault(wave_id, set()).add(age)

    scope = {
        "experiment_id": experiment_id,
        "scan_mode": scan_mode,
        "plant_age_days": plant_age_days,
    }

    if not values_by_wave:
        return {
            "trait_name": canonical_name,
            "accession_name": accession_name,
            "experiment_id": experiment_id,
            "n_waves": 0,
            "per_wave": [],
            "scope": scope,
            "note": "accession spans all waves but has no trait values matching the requested age mode",
        }

    # 8. Compute per-wave stats + age context
    per_wave: list[dict] = []
    for wave_id, values in values_by_wave.items():
        meta = wave_meta.get(wave_id, {})
        distinct_ages = sorted(ages_by_wave.get(wave_id, set()))
        per_wave.append({
            "wave_id": wave_id,
            "wave_number": meta.get("number"),
            "wave_name": meta.get("name"),
            "n": len(values),
            "mean": round(statistics.mean(values), 2),
            "std": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
            "median": round(statistics.median(values), 2),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "plant_age_days_distinct": distinct_ages,
            "plant_age_days_min": distinct_ages[0] if distinct_ages else None,
            "plant_age_days_max": distinct_ages[-1] if distinct_ages else None,
        })
    per_wave.sort(key=lambda w: w["wave_number"] if w["wave_number"] is not None else 0)

    # 9. Consistency block
    wave_medians = [w["median"] for w in per_wave]
    if len(wave_medians) >= 2:
        median_of_medians = round(statistics.median(wave_medians), 2)
        mean_of_medians = statistics.mean(wave_medians)
        std_of_medians = statistics.stdev(wave_medians)
        cv = round(std_of_medians / mean_of_medians, 4) if mean_of_medians != 0 else None
        range_of_medians = [round(min(wave_medians), 2), round(max(wave_medians), 2)]
    else:
        median_of_medians = wave_medians[0]
        cv = None
        range_of_medians = [wave_medians[0], wave_medians[0]]

    consistency = {
        "median_across_waves": median_of_medians,
        "cv_of_wave_medians": cv,
        "range_of_medians": range_of_medians,
    }

    # 10. Render chart
    fig = _wave_boxplot(per_wave, values_by_wave, canonical_name, accession_name)
    plot_url = render_and_save(fig, prefix="wave_for_accession", namespace="cyl_supabase")

    return {
        "trait_name": canonical_name,
        "accession_name": accession_name,
        "experiment_id": experiment_id,
        "n_waves": len(per_wave),
        "per_wave": per_wave,
        "consistency": consistency,
        "scope": scope,
        "plot_url": plot_url,
        "plot_layout": "boxplot",
    }


# Export all cylinder phenotyping tools
cyl_tools = [
    list_experiments_tool,
    get_experiment_by_id_tool,
    list_waves_by_experiment_tool,
    list_plants_tool,
    get_plant_by_qr_tool,
    list_scans_tool,
    get_scan_tool,
    get_scan_traits_tool,
    list_scanners_tool,
    list_phenotypers_tool,
    get_plant_scan_history_tool,
    # Analytics tools
    list_accessions_tool,
    get_plants_by_accession_tool,
    get_plant_growth_timeline_tool,
    list_traits_tool,
    compare_accessions_in_wave_tool,
    compare_waves_for_accession_tool,
]
