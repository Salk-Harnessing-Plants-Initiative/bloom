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


def _distinct_trait_names_for_experiments(experiment_ids: list[int]) -> list[str]:
    """Return deduplicated trait names present in cyl_scan_traits for the
    requested experiments, via the cyl_trait_by_experiment_wave view.

    Used by tools that accept a trait_name argument to build the candidate
    set for fuzzy resolution via _resolve_trait_name.
    """
    if not experiment_ids:
        return []
    ids_csv = ",".join(str(i) for i in experiment_ids)
    response = httpx.get(
        f"{REST_URL}/cyl_trait_by_experiment_wave",
        headers=get_headers(),
        params={
            "experiment_id": f"in.({ids_csv})",
            "select": "trait_name",
        },
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch trait names: {response.text}")
    return sorted({row["trait_name"] for row in response.json()})


@tool
def compare_trait_between_experiments_tool(
    experiment_a_id: int,
    experiment_b_id: int,
    trait_name: str,
) -> dict:
    """Compare a trait's aggregate stats between exactly two experiments.

    Returns wave-level breakdowns (count, mean, std, min, max) for the named
    trait in both experiments. Use this when the user asks how a trait
    differs across two experiments.

    Workflow:
      1. Use list_experiments_tool first to discover experiment IDs
      2. Pass the two IDs and the trait name here

    NOT for:
      - Single-experiment trait stats — use get_experiment_trait_stats_tool
      - 3+ experiment comparisons — this tool is two-experiment only.
        Tell the user that a wider comparison isn't supported yet rather
        than chaining pairwise calls (which would be statistically misleading).

    If the trait name doesn't match anything in the requested experiments,
    the tool returns a suggestions payload instead of running the query.
    Surface the suggestions to the user and retry with their selection.

    Returns:
        Success: {"rows": [...wave-level stats...], "experiment_a_name", ...}
        Trait miss: {"error": "trait not found", "trait_name", "suggestions", "sample_traits"}
        No data: {"rows": [], "note": "..."}
    """
    if experiment_a_id == experiment_b_id:
        raise ValueError(
            "compare_trait_between_experiments_tool requires two distinct experiment IDs. "
            "For single-experiment trait stats, use get_experiment_trait_stats_tool."
        )

    candidates = _distinct_trait_names_for_experiments([experiment_a_id, experiment_b_id])
    resolved = _resolve_trait_name(trait_name, candidates)
    if not resolved["matched"]:
        return {
            "error": "trait not found",
            "trait_name": trait_name,
            "suggestions": resolved["suggestions"],
            "sample_traits": resolved["sample_traits"],
        }

    ids_csv = f"{experiment_a_id},{experiment_b_id}"
    response = httpx.get(
        f"{REST_URL}/cyl_trait_by_experiment_wave",
        headers=get_headers(),
        params={
            "experiment_id": f"in.({ids_csv})",
            "trait_name": f"eq.{trait_name}",
            "select": "experiment_id,experiment_name,wave_id,wave_number,trait_name,n,mean,std,min_value,max_value",
            "order": "experiment_id.asc,wave_number.asc",
        },
    )
    if response.status_code != 200:
        raise Exception(f"Failed to compare trait: {response.text}")
    rows = response.json()

    if not rows:
        return {
            "rows": [],
            "trait_name": trait_name,
            "note": "no matching scans for this trait in either experiment",
        }

    exp_a_name = next((r["experiment_name"] for r in rows if r["experiment_id"] == experiment_a_id), None)
    exp_b_name = next((r["experiment_name"] for r in rows if r["experiment_id"] == experiment_b_id), None)

    return {
        "rows": rows,
        "trait_name": trait_name,
        "experiment_a_id": experiment_a_id,
        "experiment_a_name": exp_a_name,
        "experiment_b_id": experiment_b_id,
        "experiment_b_name": exp_b_name,
    }


_EXPERIMENT_CHIPS_LIMIT = 20

_BASELINE_EXPERIMENT_ACTIONS = [
    {"label": "List the traits", "prompt": "List the available traits"},
    {"label": "Show trait statistics", "prompt": "Show me statistics for a trait"},
    {"label": "Compare across waves", "prompt": "Compare a trait across waves"},
]


@tool
def list_experiments_tool(limit: int = 50) -> dict:
    """List all cylinder phenotyping experiments with species info."""
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


@tool
def get_trait_growth_stats_tool(qr_code: str, trait_name: str) -> dict:
    """
    Calculate pre-computed growth statistics for a specific trait on a plant.

    Args:
        qr_code: The plant's QR code identifier (e.g., 'SOY-W1-001')
        trait_name: The trait to analyze (e.g., 'root_width_max', 'primary_length')

    Returns:
        Dictionary with computed statistics:
        - first_value, last_value: Values at first and last scan
        - total_change: Absolute change (last - first)
        - percent_change: Percentage change
        - daily_growth_rate: Average change per day
        - min_value, max_value: Range of values
        - scan_count: Number of measurements
    """
    # Get plant ID from QR code
    plant_response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params={
            "qr_code": f"eq.{qr_code}",
            "select": "id,qr_code,accessions(name)"
        }
    )
    if plant_response.status_code != 200:
        raise Exception(f"Failed to get plant: {plant_response.text}")

    plants = plant_response.json()
    if not plants:
        return {"error": f"No plant found with QR code '{qr_code}'"}

    plant = plants[0]
    plant_id = plant["id"]

    # Get trait ID
    trait_response = httpx.get(
        f"{REST_URL}/cyl_traits",
        headers=get_headers(),
        params={
            "name": f"eq.{trait_name}",
            "select": "id,name"
        }
    )
    if trait_response.status_code != 200:
        raise Exception(f"Failed to get trait: {trait_response.text}")

    traits = trait_response.json()
    if not traits:
        return {"error": f"No trait found with name '{trait_name}'"}

    trait_id = traits[0]["id"]

    # Get all scan traits for this plant and trait, ordered by date
    scans_response = httpx.get(
        f"{REST_URL}/cyl_scans",
        headers=get_headers(),
        params={
            "plant_id": f"eq.{plant_id}",
            "select": "id,date_scanned,plant_age_days,cyl_scan_traits(value,trait_id)",
            "order": "date_scanned.asc"
        }
    )
    if scans_response.status_code != 200:
        raise Exception(f"Failed to get scans: {scans_response.text}")

    scans = scans_response.json()

    # Extract trait values in chronological order
    measurements = []
    for scan in scans:
        for trait_record in scan.get("cyl_scan_traits", []):
            if trait_record.get("trait_id") == trait_id:
                measurements.append({
                    "date": scan["date_scanned"],
                    "age_days": scan.get("plant_age_days"),
                    "value": trait_record.get("value")
                })

    if not measurements:
        return {"error": f"No measurements found for trait '{trait_name}' on plant '{qr_code}'"}

    # Calculate statistics
    values = [m["value"] for m in measurements if m["value"] is not None]
    first_value = values[0]
    last_value = values[-1]
    total_change = last_value - first_value
    percent_change = (total_change / first_value * 100) if first_value != 0 else None

    # Calculate daily growth rate
    first_age = measurements[0]["age_days"]
    last_age = measurements[-1]["age_days"]
    days_elapsed = last_age - first_age if first_age and last_age else None
    daily_growth_rate = (total_change / days_elapsed) if days_elapsed and days_elapsed > 0 else None

    return {
        "plant_qr_code": qr_code,
        "accession": plant.get("accessions", {}).get("name"),
        "trait_name": trait_name,
        "scan_count": len(measurements),
        "first_scan": {
            "date": measurements[0]["date"],
            "age_days": first_age,
            "value": first_value
        },
        "last_scan": {
            "date": measurements[-1]["date"],
            "age_days": last_age,
            "value": last_value
        },
        "statistics": {
            "total_change": round(total_change, 2),
            "percent_change": round(percent_change, 2) if percent_change else None,
            "daily_growth_rate": round(daily_growth_rate, 2) if daily_growth_rate else None,
            "min_value": round(min(values), 2),
            "max_value": round(max(values), 2),
            "mean_value": round(sum(values) / len(values), 2)
        }
    }


@tool
def compare_waves_trait_tool(experiment_id: int, trait_name: str) -> dict:
    """
    Compare average trait values between waves in an experiment.
    Pre-computes statistics per wave for easy comparison.

    Args:
        experiment_id: The experiment ID
        trait_name: The trait to compare (e.g., 'root_width_max', 'primary_length')

    Returns:
        Dictionary with statistics per wave and wave-to-wave comparisons.
    """
    import statistics

    # Get trait ID
    trait_response = httpx.get(
        f"{REST_URL}/cyl_traits",
        headers=get_headers(),
        params={
            "name": f"eq.{trait_name}",
            "select": "id,name"
        }
    )
    if trait_response.status_code != 200:
        raise Exception(f"Failed to get trait: {trait_response.text}")

    traits = trait_response.json()
    if not traits:
        return {"error": f"No trait found with name '{trait_name}'"}

    trait_id = traits[0]["id"]

    # Get all waves for this experiment
    waves_response = httpx.get(
        f"{REST_URL}/cyl_waves",
        headers=get_headers(),
        params={
            "experiment_id": f"eq.{experiment_id}",
            "select": "id,name,number",
            "order": "number.asc"
        }
    )
    if waves_response.status_code != 200:
        raise Exception(f"Failed to get waves: {waves_response.text}")

    waves = waves_response.json()
    if not waves:
        return {"error": f"No waves found for experiment {experiment_id}"}

    wave_stats = []

    for wave in waves:
        wave_id = wave["id"]

        # Get all plants in this wave with their scan traits
        plants_response = httpx.get(
            f"{REST_URL}/cyl_plants",
            headers=get_headers(),
            params={
                "wave_id": f"eq.{wave_id}",
                "select": "id,qr_code,cyl_scans(cyl_scan_traits(value,trait_id))"
            }
        )
        if plants_response.status_code != 200:
            continue

        plants = plants_response.json()

        # Collect all trait values for this wave
        values = []
        for plant in plants:
            for scan in plant.get("cyl_scans", []):
                for trait_record in scan.get("cyl_scan_traits", []):
                    if trait_record.get("trait_id") == trait_id and trait_record.get("value") is not None:
                        values.append(trait_record["value"])

        if values:
            wave_stats.append({
                "wave_id": wave_id,
                "wave_name": wave["name"],
                "wave_number": wave.get("number"),
                "plant_count": len(plants),
                "measurement_count": len(values),
                "statistics": {
                    "mean": round(statistics.mean(values), 2),
                    "median": round(statistics.median(values), 2),
                    "min": round(min(values), 2),
                    "max": round(max(values), 2),
                    "stddev": round(statistics.stdev(values), 2) if len(values) > 1 else 0
                }
            })

    # Calculate wave-to-wave differences
    comparisons = []
    for i in range(1, len(wave_stats)):
        prev_wave = wave_stats[i - 1]
        curr_wave = wave_stats[i]
        diff = curr_wave["statistics"]["mean"] - prev_wave["statistics"]["mean"]
        pct = (diff / prev_wave["statistics"]["mean"] * 100) if prev_wave["statistics"]["mean"] != 0 else None
        comparisons.append({
            "from_wave": prev_wave["wave_name"],
            "to_wave": curr_wave["wave_name"],
            "mean_difference": round(diff, 2),
            "percent_change": round(pct, 2) if pct else None
        })

    return {
        "experiment_id": experiment_id,
        "trait_name": trait_name,
        "waves": wave_stats,
        "wave_comparisons": comparisons
    }


@tool
def get_experiment_trait_stats_tool(experiment_id: int, trait_name: str) -> dict:
    """
    Calculate experiment-wide statistics for a trait across all plants.

    Args:
        experiment_id: The experiment ID
        trait_name: The trait to analyze (e.g., 'root_width_max', 'primary_length')

    Returns:
        Dictionary with overall statistics, distribution info, and top/bottom performers.
    """
    import statistics

    # Get trait ID
    trait_response = httpx.get(
        f"{REST_URL}/cyl_traits",
        headers=get_headers(),
        params={
            "name": f"eq.{trait_name}",
            "select": "id,name"
        }
    )
    if trait_response.status_code != 200:
        raise Exception(f"Failed to get trait: {trait_response.text}")

    traits = trait_response.json()
    if not traits:
        return {"error": f"No trait found with name '{trait_name}'"}

    trait_id = traits[0]["id"]

    # Get all waves for this experiment
    waves_response = httpx.get(
        f"{REST_URL}/cyl_waves",
        headers=get_headers(),
        params={
            "experiment_id": f"eq.{experiment_id}",
            "select": "id"
        }
    )
    if waves_response.status_code != 200:
        raise Exception(f"Failed to get waves: {waves_response.text}")

    waves = waves_response.json()
    if not waves:
        return {"error": f"No waves found for experiment {experiment_id}"}

    wave_ids = [w["id"] for w in waves]

    # Get all plants in these waves with their latest scan traits
    plants_response = httpx.get(
        f"{REST_URL}/cyl_plants",
        headers=get_headers(),
        params={
            "wave_id": f"in.({','.join(map(str, wave_ids))})",
            "select": "id,qr_code,accessions(name),cyl_scans(plant_age_days,cyl_scan_traits(value,trait_id))"
        }
    )
    if plants_response.status_code != 200:
        raise Exception(f"Failed to get plants: {plants_response.text}")

    plants = plants_response.json()

    # Collect all values and track per-plant latest values
    all_values = []
    plant_latest = []

    for plant in plants:
        plant_values = []
        latest_age = -1
        latest_value = None

        for scan in plant.get("cyl_scans", []):
            age = scan.get("plant_age_days", 0) or 0
            for trait_record in scan.get("cyl_scan_traits", []):
                if trait_record.get("trait_id") == trait_id and trait_record.get("value") is not None:
                    value = trait_record["value"]
                    all_values.append(value)
                    plant_values.append(value)
                    if age > latest_age:
                        latest_age = age
                        latest_value = value

        if latest_value is not None:
            plant_latest.append({
                "qr_code": plant["qr_code"],
                "accession": (plant.get("accessions") or {}).get("name"),
                "latest_value": latest_value,
                "age_days": latest_age
            })

    if not all_values:
        return {"error": f"No measurements found for trait '{trait_name}' in experiment {experiment_id}"}

    # Calculate statistics
    mean_val = statistics.mean(all_values)
    stddev_val = statistics.stdev(all_values) if len(all_values) > 1 else 0

    # Find outliers (> 2 stddev from mean)
    outliers = []
    for p in plant_latest:
        z_score = (p["latest_value"] - mean_val) / stddev_val if stddev_val > 0 else 0
        if abs(z_score) > 2:
            outliers.append({
                "qr_code": p["qr_code"],
                "accession": p["accession"],
                "value": round(p["latest_value"], 2),
                "z_score": round(z_score, 2),
                "direction": "high" if z_score > 0 else "low"
            })

    # Sort for top/bottom performers
    sorted_plants = sorted(plant_latest, key=lambda x: x["latest_value"], reverse=True)

    return {
        "experiment_id": experiment_id,
        "trait_name": trait_name,
        "total_measurements": len(all_values),
        "plant_count": len(plant_latest),
        "statistics": {
            "mean": round(mean_val, 2),
            "median": round(statistics.median(all_values), 2),
            "min": round(min(all_values), 2),
            "max": round(max(all_values), 2),
            "stddev": round(stddev_val, 2)
        },
        "top_5_plants": [
            {"qr_code": p["qr_code"], "accession": p["accession"], "value": round(p["latest_value"], 2)}
            for p in sorted_plants[:5]
        ],
        "bottom_5_plants": [
            {"qr_code": p["qr_code"], "accession": p["accession"], "value": round(p["latest_value"], 2)}
            for p in sorted_plants[-5:]
        ],
        "outliers": outliers
    }


_TRAIT_CHIPS_LIMIT = 20


@tool
def list_traits_tool() -> dict:
    """
    List all available phenotype traits that can be measured.
    Use this to discover what traits exist before querying data.

    Returns:
        List of trait names available in the database.
    """
    response = httpx.get(
        f"{REST_URL}/cyl_traits",
        headers=get_headers(),
        params={
            "select": "id,name",
            "order": "name.asc"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list traits: {response.text}")

    traits = response.json()
    names = [t["name"] for t in traits]
    followup_actions = [
        {"label": name, "prompt": f"Show me stats for {name}"}
        for name in names[:_TRAIT_CHIPS_LIMIT]
    ]
    return {
        "count": len(traits),
        "traits": names,
        "followup_actions": followup_actions,
        "hint": "Use these trait names with analytics tools like get_trait_growth_stats_tool or get_experiment_trait_stats_tool"
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
    get_trait_growth_stats_tool,
    compare_waves_trait_tool,
    get_experiment_trait_stats_tool,
    compare_trait_between_experiments_tool,
    compare_accessions_in_wave_tool,
    compare_waves_for_accession_tool,
]
