import type { Database } from "@/lib/database.types";

export type Species = Database["public"]["Tables"]["species"]["Row"];
export type Person = Database["public"]["Tables"]["people"]["Row"];
export type CylExperiment =
  Database["public"]["Tables"]["cyl_experiments"]["Row"];
export type TranslationProject =
  Database["public"]["Tables"]["translation_projects"]["Row"];

export type RNADataset = Database["public"]["Tables"]["scrna_datasets"]["Row"];

export type SpeciesWithExperiments = Species & {
  cyl_experiments: CylExperiment[];
};

export type SpeciesWithRNADatasets = Species & {
  scrna_datasets: RNADataset[];
};

export type TraitData =
  | {
      scan_id: number;
      date_scanned: string;
      plant_age_days: number;
      wave_number: number;
      plant_id: number;
      germ_day: number;
      plant_qr_code: string;
      accession_name: string;
      trait_name: string;
      trait_value: number;
    }[];

type CylWave = Database["public"]["Tables"]["cyl_waves"]["Row"];
type CylPlant = Database["public"]["Tables"]["cyl_plants"]["Row"];
type CylScan = Database["public"]["Tables"]["cyl_scans"]["Row"];
type CylImage = Database["public"]["Tables"]["cyl_images"]["Row"];

type Accession = Database["public"]["Tables"]["accessions"]["Row"];

type CylScanPlus = CylScan & {};
type CylPlantPlus = CylPlant & {
  cyl_scans: CylScanPlus[];
  accessions: Accession | null;
};
type CylWavePlus = CylWave & {
  cyl_plants: CylPlantPlus[];
};

export type CylExperimentPlus = CylExperiment & {
  cyl_waves: CylWavePlus[];
  species: Species | null;
  people: Person | null;
};

export type CylScanWithImages = CylScan & {
  cyl_images: CylImage[];
};
