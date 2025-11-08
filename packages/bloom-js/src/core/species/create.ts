import * as path from 'path'
import * as uuid from 'uuid'

import { DataStore } from '../../types/data-store'
import { FileUploader } from '../../types/file-uploader'
import { StorageError } from '@supabase/storage-js'
import { PostgrestError } from '@supabase/supabase-js'

export type SpeciesData = {
  common_name: string
  genus: string
  species: string
  image_path: string | undefined
}

export async function createSpecies(
  speciesList: SpeciesData[],
  uploader: FileUploader,
  db: DataStore
) {
  for (let { common_name, image_path, genus, species } of speciesList) {
    // upload to database
    const { error } = await db.addSpecies({
      common_name,
      genus,
      species,
      illustration_path: '',
    })

    if (error) {
      return { error }
    }

    // upload image
    if (image_path !== undefined) {
      const illustration_path = path.join('species', 'species_' + uuid.v4() + '.png')
      const { error } = await uploader.uploadImage(
        image_path,
        illustration_path,
        'species_illustrations'
      )
      if (error === null) {
        console.log(`Uploaded ${image_path} to ${illustration_path}`)
        const { error } = await db.updateSpecies(common_name, {
          illustration_path,
        })
        if (error) {
          return { error }
        }
      } else {
        return { error }
      }
    }
  }
  return { error: null }
}

export async function loadSpeciesData(speciesFile: string): Promise<SpeciesData[]> {
  const yaml = require('js-yaml')
  const fs = require('fs')
  const speciesList = yaml.load(fs.readFileSync(speciesFile, 'utf8')) as SpeciesData[]
  // make image paths relative to current working directory
  const relativePath = path.relative(process.cwd(), speciesFile)
  for (let speciesData of speciesList) {
    if (speciesData.image_path !== undefined) {
      speciesData.image_path = path.join(path.dirname(relativePath), speciesData.image_path)
    }
  }
  return speciesList
}

export async function createSpeciesBulk(
  speciesList: SpeciesData[],
  uploader: FileUploader,
  db: DataStore,
  opts?: {
    before?: (index: number, s: SpeciesData) => void
    result?: (index: number, error: PostgrestError | StorageError | null) => void
  }
) {
  for (let i = 0; i < speciesList.length; i++) {
    let species = speciesList[i]
    if (opts?.before) {
      opts.before(i, species)
    }
    const { error } = await createSpecies([species], uploader, db)
    if (opts?.result) {
      opts?.result(i, error)
    }
  }
}
