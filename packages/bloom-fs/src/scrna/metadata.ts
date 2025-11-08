import path from 'path'
import * as fs from 'fs'
import { FileUploader } from '@salk-hpi/bloom-js/dist/types/file-uploader'
import { DataStore } from '@salk-hpi/bloom-js/dist/types/data-store'
import { error } from 'console'
import { Json } from '../types/database.types'

export async function uploadSCRNAdata(
  filepath: string,
  filename: string,
  species_id: number,
  scientist_id: number,
  url: string,
  store: DataStore,
  assembly: string,
  annotation: string,
  strain: string,
  metadata: Json
) {
  const { created, error } = await store.insertScRna(
    filename,
    species_id,
    scientist_id,
    url,
    assembly,
    annotation,
    strain,
    metadata
  )
  return { created, error }
}

export async function uploadScRNAGenedata(
  dataset_id: number,
  scrna_gene_obj: Array<string>,
  store: DataStore
) {
  const { created, error } = await store.insertScRNAgene(dataset_id, scrna_gene_obj, store)
  return { created, error }
}

export async function uploadScRNACells(
  dataset_id: number,
  scrna_cells: Record<string, any>,
  store: DataStore
) {
  const { created, error } = await store.insertScRNAcells(dataset_id, scrna_cells, store)
  return { created, error }
}

export async function uploadScRNACounts(
  dataset_id: number,
  scrna_counts: Record<string, any>,
  store: DataStore,
  filename: string
) {
  const { created, error } = await store.insertScRNAcounts(
    dataset_id,
    scrna_counts,
    store,
    filename
  )
  return { created, error }
}
