import concurrent.futures
import os
import sys
import json

import pandas as pd
from scipy.io import mmread
from scipy.sparse import csr_matrix
from sqlalchemy import create_engine, text
import supabase

n_workers = 8

supabase_url = "http://localhost:8000"
supabase_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiaWF0IjoxNzYwNDA3NTYzLCJleHAiOjIwNzU5ODM1NjN9.MQtGFnfpIKzWTvUIDTH7IUyym8TXDW_kjcWcl-_LNgA"
database_string = "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"

engine = create_engine("postgresql+psycopg2://postgres:postgres@localhost:5432/postgres")

def process_gene(gene_idx, csr, dataset_id, dataset_name, gene_ids, supabase_url, supabase_key, engine):
    # Creating a new database connection inside the function for thread safety
    with engine.connect() as conn:
        gene_id = conn.execute(text(f'SELECT id FROM scrna_genes WHERE dataset_id = {dataset_id} AND gene_number = {gene_idx}')).fetchone()[0]

    print(f"dumping gene {gene_idx}")
    row = csr[gene_idx, :]
    nonzero = row.nonzero()
    values = row[nonzero]
    d = dict(zip(map(str, nonzero[1]), values.A.flatten()))

    gene_name = gene_ids[gene_idx]
    storage_path = f'counts/{dataset_name}/{gene_name}.json'
    
    bucket_name = 'scrna'
    json_string = json.dumps(d)
    buffer = json_string.encode('utf-8')
    # Creating a new Supabase client instance inside the function
    supabase_client = supabase.create_client(supabase_url, supabase_key)
    data = supabase_client.storage.from_(bucket_name).upload(storage_path, buffer, {"content-type": "application/json"})

    # Database operation for inserting row into scrna_counts table
    count_info = {'dataset_id': dataset_id, 'gene_id': gene_id, 'counts_object_path': storage_path}
    df = pd.DataFrame([count_info])
    with engine.begin() as conn:  # Using a transaction for the insert operation
        df.to_sql('scrna_counts', conn, if_exists='append', index=False)


def upload_scrna(dataset_dir, dataset_name, species_name):

    print(f'Uploading single-cell RNA-seq dataset {dataset_name} from {dataset_dir}...')

    # get species_id
    with engine.connect() as conn:
        species_id = conn.execute(text(f'SELECT id FROM species WHERE common_name = \'{species_name}\'')).fetchone()[0]

    # insert row into scrna_datasets table (SQLAlchemy or Supabase)
    dataset_info = {'name': dataset_name, 'species_id': species_id}
    df = pd.DataFrame([dataset_info])
    df.to_sql('scrna_datasets', engine, if_exists='append', index=False)
    with engine.connect() as conn:
        dataset_id = conn.execute(text(f'SELECT id FROM scrna_datasets WHERE name = \'{dataset_name}\'')).fetchone()[0]

    # insert rows into scrna_genes table (SQLAlchemy for bulk insert)
    gene_ids_path = os.path.join(dataset_dir, f'{dataset_name}.gene_ids.txt')
    print("GeneIds Pathway"+gene_ids_path+"\n")
   
    gene_ids = [s.strip() for s in open(gene_ids_path).read().splitlines()]
    gene_info = [{'dataset_id': dataset_id, 'gene_name': gene_id, 'gene_number': i} for (i, gene_id) in enumerate(gene_ids)]
    df = pd.DataFrame(gene_info)
    df.to_sql('scrna_genes', engine, if_exists='append', index=False)

    # insert rows into scrna_cells table (SQLAlchemy for bulk insert)
    cell_embeddings_path = os.path.join(dataset_dir, f'{dataset_name}.embeddings.tsv')
    cell_embeddings = pd.read_csv(cell_embeddings_path, sep='\t')
    cell_info = [{
        'dataset_id': dataset_id,
        'cell_number': i,
        'barcode': row.barcode,
        'x': row.x,
        'y': row.y,
        'cluster_id': row.label
    } for (i, row) in cell_embeddings.iterrows()]
    df = pd.DataFrame(cell_info)
    df.to_sql('scrna_cells', engine, if_exists='append', index=False)
    
    # insert rows into scrna_counts table (SQLAlchemy for bulk insert)
    mtx_file = os.path.join(dataset_dir, f'{dataset_name}.counts.mtx')
    coo = mmread(mtx_file)
    csr = csr_matrix(coo)

    # Usage of ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = [
            executor.submit(process_gene, gene_idx, csr, dataset_id, dataset_name, gene_ids, supabase_url, supabase_key, engine)
            for gene_idx in range(csr.shape[0])
        ]
        concurrent.futures.wait(futures)

if __name__ == '__main__':
    print(f"Total number of arguments: {len(sys.argv)}")
    print(f"Arguments received: {sys.argv}")

    dataset_dir = sys.argv[1]
    print("Directory :"+dataset_dir+"\n")
    dataset_name = sys.argv[2]
    print("dataset_name :"+dataset_name+"\n")
    species_name = sys.argv[3]
    print("species_name :"+species_name+"\n")
    upload_scrna(dataset_dir, dataset_name, species_name)