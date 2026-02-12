import { error } from 'console'
import { CylImageMetadata, DataStore } from '../../types/data-store.js'
import { Database, Json } from '../../types/database.types.js'
import { SupabaseClient } from '@supabase/supabase-js'

type TypedSupabaseClient = SupabaseClient<Database>

export type Species = Database['public']['Tables']['species']['Insert']
export type SpeciesUpdate = Database['public']['Tables']['species']['Update']

export class SupabaseStore implements DataStore {
  supabase: TypedSupabaseClient

  constructor(supabase: TypedSupabaseClient) {
    this.supabase = supabase
  }

  async addSpecies(species: Species) {
    const { error } = await this.supabase.from('species').insert(species)
    return { error }
  }

  async updateSpecies(common_name: string, fields: SpeciesUpdate) {
    const { error } = await this.supabase.from('species').update(fields).match({ common_name })
    return { error }
  }

  async getAllSpecies() {
    const { data, error } = await this.supabase.from('species').select('*')
    return { data, error }
  }

  async insertImageMetadata(metadata: CylImageMetadata) {
    // throw error if metadata has missing fields
    if (metadata.species === undefined) {
      throw new Error('missing species')
    }
    if (metadata.experiment === undefined) {
      throw new Error('missing experiment')
    }
    if (metadata.wave_number === undefined) {
      throw new Error('missing wave_number')
    }
    if (metadata.germ_day === undefined) {
      throw new Error('missing germ_day')
    }
    if (metadata.germ_day_color === undefined) {
      throw new Error('missing germ_day_color')
    }
    if (metadata.plant_age_days === undefined) {
      throw new Error('missing plant_age_days')
    }
    if (metadata.date_scanned === undefined) {
      throw new Error('missing date_scanned')
    }
    if (metadata.device_name === undefined) {
      throw new Error('missing device_name')
    }
    if (metadata.plant_qr_code === undefined) {
      throw new Error('missing plant_qr_code')
    }
    if (metadata.accession_name === undefined) {
      throw new Error('missing accession_name')
    }
    if (metadata.frame_number === undefined) {
      throw new Error('missing frame_number')
    }
    if (metadata.scientist_name === undefined) {
      throw new Error('missing scientist_name')
    }
    if (metadata.scientist_email === undefined) {
      throw new Error('missing scientist_email')
    }
    if (metadata.phenotyper_name === undefined) {
      throw new Error('missing phenotyper_name')
    }
    if (metadata.phenotyper_email === undefined) {
      throw new Error('missing phenotyper_email')
    }
    const { data, error } = await this.supabase.rpc('insert_image_v2_0', {
      species_common_name: metadata.species,
      experiment: metadata.experiment,
      wave_number: metadata.wave_number,
      germ_day: metadata.germ_day,
      germ_day_color: metadata.germ_day_color,
      plant_age_days: metadata.plant_age_days,
      date_scanned_: metadata.date_scanned,
      device_name: metadata.device_name,
      plant_qr_code: metadata.plant_qr_code,
      accession_name: metadata.accession_name,
      frame_number_: metadata.frame_number,
      phenotyper_name: metadata.phenotyper_name,
      phenotyper_email: metadata.phenotyper_email,
      scientist_name: metadata.scientist_name,
      scientist_email: metadata.scientist_email,
    })
    return { created: data, error }
  }

  async updateImageMetadata(
    image_id: number,
    fields: {
      object_path: string
      status: string
    }
  ) {
    const { error } = await this.supabase.from('cyl_images').update(fields).match({ id: image_id })
    return { error }
  }

  async getExperiments(species_id: number) {
    const { data, error } = await this.supabase
      .from('cyl_experiments')
      .select('name')
      .match({ species_id })
    return { data, error }
  }

  async getAllExperiments() {
    const { data, error } = await this.supabase
      .from('cyl_experiments')
      .select('*, species(*)')
      .order('species(common_name)')
      .order('name')
    return { data, error }
  }

  async getAllCylScanners() {
    const { data, error } = await this.supabase.from('cyl_scanners').select('*')
    return { data, error }
  }

  async addCylScanner(name: string) {
    const { error } = await this.supabase.from('cyl_scanners').insert({ name })
    return { error }
  }

  async deleteCylScanner(name: string) {
    const { error } = await this.supabase.from('cyl_scanners').delete().match({ name })
    return { error }
  }

  async insertScRna(
    dataset_name: string,
    species_id: number,
    scientist_id: number,
    url: string,
    assembly: string,
    annotation: string,
    strain: string,
    metadata: Json
  ) {
    const { data, error } = await this.supabase
      .from('scrna_datasets')
      .insert([
        {
          name: dataset_name,
          species_id: species_id,
          assembly: assembly,
          annotation: annotation,
          strain: strain,
          metadata: metadata,
          // scientist_id: scientist_id,
          // url: url
        },
      ])
      .select()

    return { created: data?.[0]?.id ?? 0, error: error || null }
  }

  async insertScRNAgene(dataset_id: number, scrna_gene_obj: Array<string>, store: DataStore) {
    const geneData = scrna_gene_obj.map((item: string, index: any) => ({
      dataset_id: dataset_id,
      gene_number: index,
      gene_name: item,
    }))

    const { data, error } = await this.supabase.from('scrna_genes').insert(geneData)

    if (error) {
      console.error('Error inserting data:', error)
    }
    return { created: true, error }
  }

  async insertScRNAcells(dataset_id: number, scRNA_cells: Record<string, any>, store: DataStore) {
    let creationStatus = { created: true, error: null }
    for (let i = 0; i < scRNA_cells.length; i++) {
      const { data, error } = await this.supabase.from('scrna_cells').insert([
        {
          dataset_id: Number(dataset_id),
          cell_number: i,
          barcode: scRNA_cells[i].id,
          x: scRNA_cells[i].c1,
          y: scRNA_cells[i].c2,
          cluster_id: scRNA_cells[i].label,
        },
      ])

      if (error) {
        console.error(
          `[ERROR] Failed to insert barcode data into scrna_table. Reason: ${
            error.message || error
          }`
        )
        creationStatus = { created: false, error: null }
        break
      }
    }
    return creationStatus
  }

  async insertScRNAcounts(
    dataset_id: number,
    scRNA_counts: Record<string, any>,
    store: DataStore,
    filename: string
  ) {
    //Creating geneId : [{cells-expressions},{cells-expressions}] pais from counts json
    const result: Record<number, { [key: number]: number }[]> = {}
    for (let i = 0; i < scRNA_counts.length; i++) {
      let gene_index = scRNA_counts[i][0]
      let barcode_index = scRNA_counts[i][1]
      let expression = scRNA_counts[i][2]

      if (!result[gene_index]) {
        result[gene_index] = []
      }
      result[gene_index].push({ [barcode_index]: expression })
    }

    let creationStatus = { created: true, error: null }

    for (const key in result) {
      try {
        const { data: gene_id, error: geneid_table_error } = await this.supabase
          .from('scrna_genes')
          .select('id, gene_name')
          .eq('dataset_id', dataset_id)
          .eq('gene_number', Number(key) - 1)

        if (!gene_id || gene_id.length === 0) {
          console.error(
            `[ERROR] Reason : Failed to locate gene in gene table while uploading counts.`
          )
          creationStatus = { created: false, error: null }
          break
        }
        let clean_filename = filename
          .trim()
          .replace(/^\\+/, '')
          .replace(/\s+/g, '_')
          .replace(/[<>:"|?*\\%]/g, '')
          .replace(/\.json$/, '')
        let gene_name = gene_id[0].gene_name
        let clean_genename = gene_name.replace(/[\/:*?"<>|\\\s]+/g, '_')
        let storage_path = `counts/${clean_filename}_${dataset_id}_/${clean_genename}.json`
        let bucket_name = 'scrna'
        let json_string = JSON.stringify(result[key])
        let fileBuffer = Buffer.from(json_string, 'utf-8')
        console.log('dumping gene:' + key)

        const { error } = await this.supabase.storage
          .from(bucket_name)
          .upload(storage_path, fileBuffer, { contentType: 'application/json' })
        if (error) {
          console.error(`[ERROR] Supbase S3 file storege error. Reason:${error.message}`)
          creationStatus = { created: false, error: null }
          break
        }

        const row_data = {
          dataset_id: Number(dataset_id),
          gene_id: gene_id[0]?.id,
          counts_object_path: storage_path,
        }

        const { data, error: supabase_erro } = await this.supabase
          .from('scrna_counts')
          .insert([row_data])
        if (supabase_erro) {
          console.error(`[ERROR] Error connecting to supbase. Reason:${error}`)
          creationStatus = { created: false, error: null }
          break
        }
      } catch (error) {
        creationStatus = { created: false, error: null }
        break
      }
    }
    return creationStatus
  }
}
