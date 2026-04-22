"""
scRNA-seq (Single-cell RNA sequencing) tools for querying datasets, genes, cells, and expression data.
"""
import json
import httpx
from langchain_core.tools import tool
from .base import REST_URL, get_headers
from config import get_supabase_client


@tool
def get_all_datasets_tool() -> list:
    """Fetch all single-cell RNA-seq datasets with species info.
    Returns dataset id, name, species common_name, genus, species.
    """
    response = httpx.get(
        f"{REST_URL}/scrna_datasets",
        headers=get_headers(),
        params={"select": "id,name,strain,assembly,annotation,species(id,common_name,genus,species)"}
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch datasets: {response.text}")
    return response.json()


@tool
def get_dataset_by_id_tool(dataset_id: int) -> dict:
    """Fetch a single-cell dataset by its ID with species info."""
    response = httpx.get(
        f"{REST_URL}/scrna_datasets",
        headers=get_headers(),
        params={
            "id": f"eq.{dataset_id}",
            "select": "id,name,strain,assembly,annotation,species(id,common_name,genus,species)"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch dataset: {response.text}")
    data = response.json()
    return data[0] if data else {}


@tool
def search_datasets_by_species_tool(species_name: str) -> list:
    """Search datasets by species common name (e.g., 'Soybean', 'Rice').
    First finds the species, then returns datasets for that species.
    """
    # First find the species
    species_resp = httpx.get(
        f"{REST_URL}/species",
        headers=get_headers(),
        params={"common_name": f"ilike.*{species_name}*", "select": "id,common_name"}
    )
    if species_resp.status_code != 200:
        raise Exception(f"Failed to search species: {species_resp.text}")

    species_list = species_resp.json()
    if not species_list:
        return []

    # Get datasets for these species
    species_ids = [s["id"] for s in species_list]
    response = httpx.get(
        f"{REST_URL}/scrna_datasets",
        headers=get_headers(),
        params={
            "species_id": f"in.({','.join(map(str, species_ids))})",
            "select": "id,name,strain,assembly,species(common_name,genus,species)"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch datasets: {response.text}")
    return response.json()


@tool
def get_clusters_by_dataset_tool(dataset_id: int) -> list:
    """Fetch all clusters for a given dataset from the authoritative
    scrna_clusters catalog (populated by upload_scrna_v2.py).

    Returns cluster_id, name, color, ordinal, and cell_count per cluster.
    cell_count comes from scrna_cluster_stats when precomputed; falls
    back to an aggregation over scrna_cells when stats are absent
    (legacy datasets ingested by the v1 pipeline).
    """
    # Primary path: the Phase 1 catalog + precomputed stats.
    clusters_resp = httpx.get(
        f"{REST_URL}/scrna_clusters",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "select": "cluster_id,name,color,ordinal",
            "order": "ordinal.asc",
        },
    )
    if clusters_resp.status_code != 200:
        raise Exception(f"Failed to fetch clusters: {clusters_resp.text}")
    clusters = clusters_resp.json()

    if clusters:
        stats_resp = httpx.get(
            f"{REST_URL}/scrna_cluster_stats",
            headers=get_headers(),
            params={
                "dataset_id": f"eq.{dataset_id}",
                "select": "cluster_id,cell_count",
            },
        )
        counts = {}
        if stats_resp.status_code == 200:
            for row in stats_resp.json():
                counts[row["cluster_id"]] = row["cell_count"]
        return [
            {
                "cluster_id": c["cluster_id"],
                "name": c.get("name"),
                "color": c.get("color"),
                "ordinal": c.get("ordinal"),
                "cell_count": counts.get(c["cluster_id"]),
            }
            for c in clusters
        ]

    # Fallback for legacy datasets (no scrna_clusters rows): aggregate
    # distinct cluster_id values from scrna_cells. Preserves prior
    # behaviour for datasets ingested by scripts/upload_scrna.py.
    response = httpx.get(
        f"{REST_URL}/scrna_cells",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "select": "cluster_id",
        },
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch clusters: {response.text}")
    cells = response.json()
    cluster_counts = {}
    for cell in cells:
        cid = cell.get("cluster_id", "unknown")
        cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
    return [
        {"cluster_id": k, "name": k, "color": None,
         "ordinal": None, "cell_count": v}
        for k, v in sorted(cluster_counts.items())
    ]


@tool
def get_genes_by_dataset_tool(dataset_id: int, limit: int = 50) -> list:
    """Fetch genes for a dataset. Returns gene id, gene_number, gene_name."""
    response = httpx.get(
        f"{REST_URL}/scrna_genes",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "select": "id,gene_number,gene_name",
            "limit": limit
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch genes: {response.text}")
    return response.json()


@tool
def search_gene_tool(dataset_id: int, gene_name: str) -> list:
    """Search for a gene by name in a dataset."""
    response = httpx.get(
        f"{REST_URL}/scrna_genes",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "gene_name": f"ilike.*{gene_name}*",
            "select": "id,gene_number,gene_name"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to search genes: {response.text}")
    return response.json()


@tool
def get_differential_expression_files_tool(dataset_id: int) -> list:
    """Get differential expression file paths for a dataset.
    The DE data is stored in files, this returns the file paths and cluster info.
    """
    response = httpx.get(
        f"{REST_URL}/scrna_de",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "select": "id,file_path,cluster_id"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch DE files: {response.text}")
    return response.json()


@tool
def get_top_de_genes_tool(dataset_id: int, cluster_id: str, top_n: int = 20) -> list:
    """Get top differentially expressed genes for a specific cluster.

    Args:
        dataset_id: The dataset ID
        cluster_id: The cluster name/ID (e.g., 'Cortex', 'Cluster1')
        top_n: Number of top genes to return (default 20)

    Returns:
        List of top DE genes with gene name, log2 fold change, p-value, and expression percentages.
    """
    # Get the file path from scrna_de
    response = httpx.get(
        f"{REST_URL}/scrna_de",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "cluster_id": f"eq.{cluster_id}",
            "select": "file_path"
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch DE file path: {response.text}")

    results = response.json()
    if not results:
        raise Exception(f"No DE results found for dataset {dataset_id}, cluster {cluster_id}")

    file_path = results[0]["file_path"]

    # Fetch the JSON file from storage using shared client
    client = get_supabase_client()

    try:
        file_content = client.storage.from_("scrna").download(file_path)
        de_results = json.loads(file_content.decode("utf-8"))
    except Exception as e:
        raise Exception(f"Failed to download DE results from storage: {e}")

    # Return top N genes
    return de_results[:top_n]


@tool
def get_cells_by_cluster_tool(dataset_id: int, cluster_id: str, limit: int = 100) -> list:
    """Get cells for a specific cluster in a dataset.
    Returns cell info including coordinates and barcode.
    """
    response = httpx.get(
        f"{REST_URL}/scrna_cells",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "cluster_id": f"eq.{cluster_id}",
            "select": "id,cell_number,barcode,x,y,replicate",
            "limit": limit
        }
    )
    if response.status_code != 200:
        raise Exception(f"Failed to fetch cells: {response.text}")
    return response.json()


@tool
def get_gene_counts_tool(dataset_id: int, gene_name: str) -> dict:
    """Get expression counts for a specific gene across cells.

    Fetches count data from storage (JSON file) for the specified gene.
    Returns dict with cell_number as key and count as value.

    Args:
        dataset_id: The dataset ID
        gene_name: The gene name (e.g., 'Glyma.01G000100')

    Returns:
        Dictionary with gene info and counts per cell.
    """
    # First get gene_id and counts file path
    gene_resp = httpx.get(
        f"{REST_URL}/scrna_genes",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "gene_name": f"eq.{gene_name}",
            "select": "id,gene_name"
        }
    )
    if gene_resp.status_code != 200 or not gene_resp.json():
        raise Exception(f"Gene '{gene_name}' not found in dataset {dataset_id}")

    gene_data = gene_resp.json()[0]
    gene_id = gene_data["id"]

    # Get the counts file path from scrna_counts table
    counts_resp = httpx.get(
        f"{REST_URL}/scrna_counts",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "gene_id": f"eq.{gene_id}",
            "select": "counts_object_path"
        }
    )
    if counts_resp.status_code != 200 or not counts_resp.json():
        raise Exception(f"No counts file found for gene '{gene_name}' in dataset {dataset_id}")

    file_path = counts_resp.json()[0]["counts_object_path"]

    # Fetch the JSON file from storage
    client = get_supabase_client()

    try:
        file_content = client.storage.from_("scrna").download(file_path)
        counts_data = json.loads(file_content.decode("utf-8"))
    except Exception as e:
        raise Exception(f"Failed to download counts from storage: {e}")

    return {
        "gene_name": gene_name,
        "gene_id": gene_id,
        "dataset_id": dataset_id,
        "counts": counts_data  # Dict of cell_number -> count
    }


@tool
def get_gene_expression_by_cluster_tool(dataset_id: int, gene_name: str) -> dict:
    """Find which cell types/clusters express a specific gene.

    This tool answers questions like "In which cell types is gene X expressed?"
    It fetches gene expression counts and maps them to clusters.

    Args:
        dataset_id: The dataset ID
        gene_name: The gene name (e.g., 'Glyma.01G000100')

    Returns:
        Dictionary with cluster expression summary:
        - clusters: list of {cluster_id, expressing_cells, total_cells, percent_expressing, mean_expression}
        - gene_name: the queried gene
        - total_expressing_cells: cells with non-zero expression
    """
    # First get gene counts
    gene_resp = httpx.get(
        f"{REST_URL}/scrna_genes",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "gene_name": f"eq.{gene_name}",
            "select": "id,gene_name"
        }
    )
    if gene_resp.status_code != 200 or not gene_resp.json():
        raise Exception(f"Gene '{gene_name}' not found in dataset {dataset_id}")

    gene_data = gene_resp.json()[0]
    gene_id = gene_data["id"]

    # Get the counts file path
    counts_resp = httpx.get(
        f"{REST_URL}/scrna_counts",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "gene_id": f"eq.{gene_id}",
            "select": "counts_object_path"
        }
    )
    if counts_resp.status_code != 200 or not counts_resp.json():
        raise Exception(f"No counts file found for gene '{gene_name}' in dataset {dataset_id}")

    file_path = counts_resp.json()[0]["counts_object_path"]

    # Fetch counts from storage
    client = get_supabase_client()
    try:
        file_content = client.storage.from_("scrna").download(file_path)
        counts_data = json.loads(file_content.decode("utf-8"))
    except Exception as e:
        raise Exception(f"Failed to download counts from storage: {e}")

    # Get all cells with their cluster assignments
    cells_resp = httpx.get(
        f"{REST_URL}/scrna_cells",
        headers=get_headers(),
        params={
            "dataset_id": f"eq.{dataset_id}",
            "select": "cell_number,cluster_id"
        }
    )
    if cells_resp.status_code != 200:
        raise Exception(f"Failed to fetch cells: {cells_resp.text}")

    cells = cells_resp.json()

    # Build cell_number -> cluster_id mapping
    cell_to_cluster = {str(c["cell_number"]): c["cluster_id"] for c in cells}

    # Count expression per cluster
    cluster_stats = {}
    for cell_num, count in counts_data.items():
        cluster_id = cell_to_cluster.get(str(cell_num), "unknown")
        if cluster_id not in cluster_stats:
            cluster_stats[cluster_id] = {"expressing": 0, "total": 0, "sum_expression": 0}
        cluster_stats[cluster_id]["total"] += 1
        if count > 0:
            cluster_stats[cluster_id]["expressing"] += 1
            cluster_stats[cluster_id]["sum_expression"] += count

    # Also count total cells per cluster (including non-expressing)
    for cell in cells:
        cluster_id = cell["cluster_id"]
        if cluster_id not in cluster_stats:
            cluster_stats[cluster_id] = {"expressing": 0, "total": 0, "sum_expression": 0}

    # Calculate stats per cluster
    result_clusters = []
    for cluster_id, stats in sorted(cluster_stats.items()):
        total_in_cluster = sum(1 for c in cells if c["cluster_id"] == cluster_id)
        expressing = stats["expressing"]
        pct = round(100 * expressing / total_in_cluster, 1) if total_in_cluster > 0 else 0
        mean_expr = round(stats["sum_expression"] / expressing, 2) if expressing > 0 else 0

        result_clusters.append({
            "cluster_id": cluster_id,
            "expressing_cells": expressing,
            "total_cells": total_in_cluster,
            "percent_expressing": pct,
            "mean_expression": mean_expr
        })

    # Sort by percent expressing (descending)
    result_clusters.sort(key=lambda x: x["percent_expressing"], reverse=True)

    total_expressing = sum(c["expressing_cells"] for c in result_clusters)

    return {
        "gene_name": gene_name,
        "dataset_id": dataset_id,
        "total_expressing_cells": total_expressing,
        "clusters": result_clusters
    }


# Export all scRNA-seq tools
scrna_tools = [
    get_all_datasets_tool,
    get_dataset_by_id_tool,
    search_datasets_by_species_tool,
    get_clusters_by_dataset_tool,
    get_genes_by_dataset_tool,
    search_gene_tool,
    get_differential_expression_files_tool,
    get_top_de_genes_tool,
    get_cells_by_cluster_tool,
    get_gene_counts_tool,
    get_gene_expression_by_cluster_tool,
]
