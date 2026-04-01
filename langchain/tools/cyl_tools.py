"""
Cylinder phenotyping tools for querying experiments, plants, scans, and traits.
"""
from typing import Optional
import httpx
from langchain_core.tools import tool
from .base import REST_URL, get_headers


@tool
def list_experiments_tool(limit: int = 50) -> list:
    """List all cylinder phenotyping experiments with species info."""
    response = httpx.get(
        f"{REST_URL}/cyl_experiments",
        headers=get_headers(),
        params={
            "select": "id,name,created_at,slack_channel,species(id,common_name),people(id,name)",
            "limit": limit
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to list experiments: {response.text}")
    return response.json()


@tool
def get_experiment_by_id_tool(experiment_id: int) -> dict:
    """Get a cylinder experiment by ID with full details."""
    response = httpx.get(
        f"{REST_URL}/cyl_experiments",
        headers=get_headers(),
        params={
            "id": f"eq.{experiment_id}",
            "select": "id,name,created_at,slack_channel,species(id,common_name,genus,species),people(id,name,email)"
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
                "accession": plant.get("accessions", {}).get("name"),
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
    return {
        "count": len(traits),
        "traits": [t["name"] for t in traits],
        "hint": "Use these trait names with analytics tools like get_trait_growth_stats_tool or get_experiment_trait_stats_tool"
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
]
