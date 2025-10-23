export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  public: {
    Tables: {
      accessions: {
        Row: {
          created_at: string
          id: number
          name: string
        }
        Insert: {
          created_at?: string
          id?: number
          name: string
        }
        Update: {
          created_at?: string
          id?: number
          name?: string
        }
        Relationships: []
      }
      assemblies: {
        Row: {
          accession_name: string | null
          archive_link: string
          external_link: string | null
          external_version: string | null
          hpi_assembly: string | null
          hpi_reference_id: string | null
          id: number
          origin: string | null
          prefix: string | null
          species_id: number | null
          version: number | null
        }
        Insert: {
          accession_name?: string | null
          archive_link: string
          external_link?: string | null
          external_version?: string | null
          hpi_assembly?: string | null
          hpi_reference_id?: string | null
          id?: number
          origin?: string | null
          prefix?: string | null
          species_id?: number | null
          version?: number | null
        }
        Update: {
          accession_name?: string | null
          archive_link?: string
          external_link?: string | null
          external_version?: string | null
          hpi_assembly?: string | null
          hpi_reference_id?: string | null
          id?: number
          origin?: string | null
          prefix?: string | null
          species_id?: number | null
          version?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "assemblies_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["species_id"]
          },
          {
            foreignKeyName: "assemblies_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["species_id"]
          },
          {
            foreignKeyName: "assemblies_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "species"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_camera_settings: {
        Row: {
          id: string
          scanner_brightness: number
          scanner_contrast: number
          scanner_exposure_time: number
          scanner_gain: number
          scanner_gamma: number
          scanner_seconds_per_rot: number
        }
        Insert: {
          id?: string
          scanner_brightness: number
          scanner_contrast: number
          scanner_exposure_time: number
          scanner_gain: number
          scanner_gamma: number
          scanner_seconds_per_rot: number
        }
        Update: {
          id?: string
          scanner_brightness?: number
          scanner_contrast?: number
          scanner_exposure_time?: number
          scanner_gain?: number
          scanner_gamma?: number
          scanner_seconds_per_rot?: number
        }
        Relationships: []
      }
      cyl_dataset_traits: {
        Row: {
          dataset_id: number
          trait_id: number
        }
        Insert: {
          dataset_id: number
          trait_id: number
        }
        Update: {
          dataset_id?: number
          trait_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "cyl_dataset_traits_dataset_id_fkey"
            columns: ["dataset_id"]
            isOneToOne: false
            referencedRelation: "cyl_datasets"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_dataset_traits_trait_id_fkey"
            columns: ["trait_id"]
            isOneToOne: false
            referencedRelation: "cyl_scan_traits"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_datasets: {
        Row: {
          created_at: string
          cyl_qc_set_id: number | null
          experiment_id: number | null
          id: number
          name: string
          timepoints: Json | null
          trait_source_id: number | null
        }
        Insert: {
          created_at?: string
          cyl_qc_set_id?: number | null
          experiment_id?: number | null
          id?: number
          name: string
          timepoints?: Json | null
          trait_source_id?: number | null
        }
        Update: {
          created_at?: string
          cyl_qc_set_id?: number | null
          experiment_id?: number | null
          id?: number
          name?: string
          timepoints?: Json | null
          trait_source_id?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_datasets_cyl_qc_set_id_fkey"
            columns: ["cyl_qc_set_id"]
            isOneToOne: false
            referencedRelation: "cyl_qc_sets"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_datasets_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_experiments"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_datasets_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["experiment_id"]
          },
          {
            foreignKeyName: "cyl_datasets_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["experiment_id"]
          },
          {
            foreignKeyName: "cyl_datasets_trait_source_id_fkey"
            columns: ["trait_source_id"]
            isOneToOne: false
            referencedRelation: "cyl_trait_sources"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_experiments: {
        Row: {
          created_at: string
          deleted: boolean | null
          description: string | null
          id: number
          name: string
          scientist_id: number | null
          slack_channel_url: string | null
          species_id: number | null
        }
        Insert: {
          created_at?: string
          deleted?: boolean | null
          description?: string | null
          id?: number
          name: string
          scientist_id?: number | null
          slack_channel_url?: string | null
          species_id?: number | null
        }
        Update: {
          created_at?: string
          deleted?: boolean | null
          description?: string | null
          id?: number
          name?: string
          scientist_id?: number | null
          slack_channel_url?: string | null
          species_id?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_experiments_scientist_id_fkey"
            columns: ["scientist_id"]
            isOneToOne: false
            referencedRelation: "people"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_experiments_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["species_id"]
          },
          {
            foreignKeyName: "cyl_experiments_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["species_id"]
          },
          {
            foreignKeyName: "cyl_experiments_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "species"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_image_traits: {
        Row: {
          id: number
          image_id: number
          name: string
          value: number | null
        }
        Insert: {
          id?: number
          image_id: number
          name: string
          value?: number | null
        }
        Update: {
          id?: number
          image_id?: number
          name?: string
          value?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_image_traits_image_id_fkey"
            columns: ["image_id"]
            isOneToOne: false
            referencedRelation: "cyl_images"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_images: {
        Row: {
          date_scanned: string | null
          frame_number: number | null
          id: number
          object_path: string | null
          scan_id: number | null
          status: string | null
          uploaded_at: string | null
        }
        Insert: {
          date_scanned?: string | null
          frame_number?: number | null
          id?: number
          object_path?: string | null
          scan_id?: number | null
          status?: string | null
          uploaded_at?: string | null
        }
        Update: {
          date_scanned?: string | null
          frame_number?: number | null
          id?: number
          object_path?: string | null
          scan_id?: number | null
          status?: string | null
          uploaded_at?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_images_scan_id_fkey"
            columns: ["scan_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_images_scan_id_fkey"
            columns: ["scan_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["scan_id"]
          },
        ]
      }
      cyl_plant_metadata: {
        Row: {
          id: string
          packet: string | null
          planting_date: string | null
          plating_date: string | null
          position: string | null
        }
        Insert: {
          id?: string
          packet?: string | null
          planting_date?: string | null
          plating_date?: string | null
          position?: string | null
        }
        Update: {
          id?: string
          packet?: string | null
          planting_date?: string | null
          plating_date?: string | null
          position?: string | null
        }
        Relationships: []
      }
      cyl_plants: {
        Row: {
          accession_id: number | null
          created_at: string
          germ_day: number | null
          germ_day_color: string | null
          id: number
          qr_code: string | null
          wave_id: number | null
        }
        Insert: {
          accession_id?: number | null
          created_at?: string
          germ_day?: number | null
          germ_day_color?: string | null
          id?: number
          qr_code?: string | null
          wave_id?: number | null
        }
        Update: {
          accession_id?: number | null
          created_at?: string
          germ_day?: number | null
          germ_day_color?: string | null
          id?: number
          qr_code?: string | null
          wave_id?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_plants_accession_id_fkey"
            columns: ["accession_id"]
            isOneToOne: false
            referencedRelation: "accessions"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_plants_wave_id_fkey"
            columns: ["wave_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["wave_id"]
          },
          {
            foreignKeyName: "cyl_plants_wave_id_fkey"
            columns: ["wave_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["wave_id"]
          },
          {
            foreignKeyName: "cyl_plants_wave_id_fkey"
            columns: ["wave_id"]
            isOneToOne: false
            referencedRelation: "cyl_waves"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_plants_metadata: {
        Row: {
          avg_root_mass: number | null
          avg_root_shoot_ratio: number | null
          avg_shoot_mass: number | null
          group_id: string | null
          id: string
          packet: string | null
          plant_count: number | null
          planting_date: string | null
          plating_date: string | null
          position: string | null
          root_mass: number | null
          shoot_mass: number | null
          tubemass_no_cap_empty_root: number | null
          tubemass_plus_root: number | null
          tubemass_plus_shoot: number | null
        }
        Insert: {
          avg_root_mass?: number | null
          avg_root_shoot_ratio?: number | null
          avg_shoot_mass?: number | null
          group_id?: string | null
          id?: string
          packet?: string | null
          plant_count?: number | null
          planting_date?: string | null
          plating_date?: string | null
          position?: string | null
          root_mass?: number | null
          shoot_mass?: number | null
          tubemass_no_cap_empty_root?: number | null
          tubemass_plus_root?: number | null
          tubemass_plus_shoot?: number | null
        }
        Update: {
          avg_root_mass?: number | null
          avg_root_shoot_ratio?: number | null
          avg_shoot_mass?: number | null
          group_id?: string | null
          id?: string
          packet?: string | null
          plant_count?: number | null
          planting_date?: string | null
          plating_date?: string | null
          position?: string | null
          root_mass?: number | null
          shoot_mass?: number | null
          tubemass_no_cap_empty_root?: number | null
          tubemass_plus_root?: number | null
          tubemass_plus_shoot?: number | null
        }
        Relationships: []
      }
      cyl_qc_codes: {
        Row: {
          created_at: string
          id: number
          plant_id: number
          value: string
        }
        Insert: {
          created_at?: string
          id?: number
          plant_id: number
          value: string
        }
        Update: {
          created_at?: string
          id?: number
          plant_id?: number
          value?: string
        }
        Relationships: [
          {
            foreignKeyName: "cyl_qc_codes_plant_id_fkey"
            columns: ["plant_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_qc_codes_plant_id_fkey"
            columns: ["plant_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["plant_id"]
          },
          {
            foreignKeyName: "cyl_qc_codes_plant_id_fkey"
            columns: ["plant_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["plant_id"]
          },
        ]
      }
      cyl_qc_set_codes: {
        Row: {
          code_id: number
          id: number
          set_id: number
        }
        Insert: {
          code_id: number
          id?: number
          set_id: number
        }
        Update: {
          code_id?: number
          id?: number
          set_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "cyl_qc_set_codes_code_id_fkey"
            columns: ["code_id"]
            isOneToOne: false
            referencedRelation: "cyl_qc_codes"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_qc_set_codes_set_id_fkey"
            columns: ["set_id"]
            isOneToOne: false
            referencedRelation: "cyl_qc_sets"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_qc_sets: {
        Row: {
          created_at: string
          experiment_id: number | null
          id: number
          name: string
          notes: string | null
        }
        Insert: {
          created_at?: string
          experiment_id?: number | null
          id?: number
          name: string
          notes?: string | null
        }
        Update: {
          created_at?: string
          experiment_id?: number | null
          id?: number
          name?: string
          notes?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_qc_sets_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_experiments"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_qc_sets_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["experiment_id"]
          },
          {
            foreignKeyName: "cyl_qc_sets_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["experiment_id"]
          },
        ]
      }
      cyl_scan_traits: {
        Row: {
          id: number
          scan_id: number
          source_id: number | null
          trait_id: number | null
          value: number | null
        }
        Insert: {
          id?: number
          scan_id: number
          source_id?: number | null
          trait_id?: number | null
          value?: number | null
        }
        Update: {
          id?: number
          scan_id?: number
          source_id?: number | null
          trait_id?: number | null
          value?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_scan_traits_scan_id_fkey"
            columns: ["scan_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_scan_traits_scan_id_fkey"
            columns: ["scan_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["scan_id"]
          },
          {
            foreignKeyName: "cyl_scan_traits_source_id_fkey"
            columns: ["source_id"]
            isOneToOne: false
            referencedRelation: "cyl_trait_sources"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_scan_traits_trait_id_fkey"
            columns: ["trait_id"]
            isOneToOne: false
            referencedRelation: "cyl_traits"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_scanners: {
        Row: {
          id: number
          name: string | null
        }
        Insert: {
          id?: number
          name?: string | null
        }
        Update: {
          id?: number
          name?: string | null
        }
        Relationships: []
      }
      cyl_scans: {
        Row: {
          cyl_camera_settings_id: string | null
          date_scanned: string | null
          id: number
          phenotyper_id: number | null
          plant_age_days: number | null
          plant_id: number | null
          scanner_id: number | null
          scientist_id: number | null
          uploaded_at: string | null
        }
        Insert: {
          cyl_camera_settings_id?: string | null
          date_scanned?: string | null
          id?: number
          phenotyper_id?: number | null
          plant_age_days?: number | null
          plant_id?: number | null
          scanner_id?: number | null
          scientist_id?: number | null
          uploaded_at?: string | null
        }
        Update: {
          cyl_camera_settings_id?: string | null
          date_scanned?: string | null
          id?: number
          phenotyper_id?: number | null
          plant_age_days?: number | null
          plant_id?: number | null
          scanner_id?: number | null
          scientist_id?: number | null
          uploaded_at?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_scans_cyl_camera_settings_id_fkey"
            columns: ["cyl_camera_settings_id"]
            isOneToOne: false
            referencedRelation: "cyl_camera_settings"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_scans_phenotyper_id_fkey"
            columns: ["phenotyper_id"]
            isOneToOne: false
            referencedRelation: "phenotypers"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_scans_plant_id_fkey"
            columns: ["plant_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_scans_plant_id_fkey"
            columns: ["plant_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["plant_id"]
          },
          {
            foreignKeyName: "cyl_scans_plant_id_fkey"
            columns: ["plant_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["plant_id"]
          },
          {
            foreignKeyName: "cyl_scans_scanner_id_fkey"
            columns: ["scanner_id"]
            isOneToOne: false
            referencedRelation: "cyl_scanners"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "fk_cyl_scans_scientist"
            columns: ["scientist_id"]
            isOneToOne: false
            referencedRelation: "cyl_scientists"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_scientists: {
        Row: {
          email: string | null
          id: number
          scientist_name: string | null
        }
        Insert: {
          email?: string | null
          id?: number
          scientist_name?: string | null
        }
        Update: {
          email?: string | null
          id?: number
          scientist_name?: string | null
        }
        Relationships: []
      }
      cyl_trait_sources: {
        Row: {
          id: number
          name: string
        }
        Insert: {
          id?: number
          name: string
        }
        Update: {
          id?: number
          name?: string
        }
        Relationships: []
      }
      cyl_traits: {
        Row: {
          id: number
          name: string
        }
        Insert: {
          id?: number
          name: string
        }
        Update: {
          id?: number
          name?: string
        }
        Relationships: []
      }
      cyl_waves: {
        Row: {
          experiment_id: number | null
          id: number
          name: string | null
          number: number | null
        }
        Insert: {
          experiment_id?: number | null
          id?: number
          name?: string | null
          number?: number | null
        }
        Update: {
          experiment_id?: number | null
          id?: number
          name?: string | null
          number?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_waves_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_experiments"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_waves_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["experiment_id"]
          },
          {
            foreignKeyName: "cyl_waves_experiment_id_fkey"
            columns: ["experiment_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["experiment_id"]
          },
        ]
      }
      experiment_progress_logs: {
        Row: {
          gene: string
          id: number
          images: Json | null
          links: Json | null
          message: string
          tags: Json | null
          timestamp: string
          user_email: string | null
        }
        Insert: {
          gene: string
          id?: number
          images?: Json | null
          links?: Json | null
          message: string
          tags?: Json | null
          timestamp?: string
          user_email?: string | null
        }
        Update: {
          gene?: string
          id?: number
          images?: Json | null
          links?: Json | null
          message?: string
          tags?: Json | null
          timestamp?: string
          user_email?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "fk_gene"
            columns: ["gene"]
            isOneToOne: false
            referencedRelation: "gene_candidates"
            referencedColumns: ["gene"]
          },
        ]
      }
      gene_candidate_scientists: {
        Row: {
          gene_candidate_id: string
          id: number
          scientist_id: number
        }
        Insert: {
          gene_candidate_id: string
          id?: number
          scientist_id: number
        }
        Update: {
          gene_candidate_id?: string
          id?: number
          scientist_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "gene_candidate_scientists_gene_candidate_id_fkey"
            columns: ["gene_candidate_id"]
            isOneToOne: false
            referencedRelation: "gene_candidates"
            referencedColumns: ["gene"]
          },
          {
            foreignKeyName: "gene_candidate_scientists_scientist_id_fkey"
            columns: ["scientist_id"]
            isOneToOne: false
            referencedRelation: "people"
            referencedColumns: ["id"]
          },
        ]
      }
      gene_candidate_support: {
        Row: {
          candidate_id: string | null
          description: string | null
          primary_key: number
          source: string | null
        }
        Insert: {
          candidate_id?: string | null
          description?: string | null
          primary_key?: number
          source?: string | null
        }
        Update: {
          candidate_id?: string | null
          description?: string | null
          primary_key?: number
          source?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "gene_candidate_support_gene_fkey"
            columns: ["candidate_id"]
            isOneToOne: false
            referencedRelation: "gene_candidates"
            referencedColumns: ["gene"]
          },
        ]
      }
      gene_candidates: {
        Row: {
          category: string | null
          created_at: string
          disclosed_to_otd: boolean | null
          evidence_description: string | null
          experiment_plans_and_progress: string | null
          experiment_progress_logs: Json | null
          gene: string
          publication_status: boolean | null
          status: string
          status_logs: Json | null
          translation_approval_date: string | null
        }
        Insert: {
          category?: string | null
          created_at?: string
          disclosed_to_otd?: boolean | null
          evidence_description?: string | null
          experiment_plans_and_progress?: string | null
          experiment_progress_logs?: Json | null
          gene: string
          publication_status?: boolean | null
          status?: string
          status_logs?: Json | null
          translation_approval_date?: string | null
        }
        Update: {
          category?: string | null
          created_at?: string
          disclosed_to_otd?: boolean | null
          evidence_description?: string | null
          experiment_plans_and_progress?: string | null
          experiment_progress_logs?: Json | null
          gene?: string
          publication_status?: boolean | null
          status?: string
          status_logs?: Json | null
          translation_approval_date?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "gene_candidates_gene_fkey"
            columns: ["gene"]
            isOneToOne: true
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          },
        ]
      }
      gene_orthologs: {
        Row: {
          gene_x: string
          gene_y: string
        }
        Insert: {
          gene_x: string
          gene_y: string
        }
        Update: {
          gene_x?: string
          gene_y?: string
        }
        Relationships: [
          {
            foreignKeyName: "gene_orthologs_gene_x_fkey"
            columns: ["gene_x"]
            isOneToOne: false
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          },
          {
            foreignKeyName: "gene_orthologs_gene_y_fkey"
            columns: ["gene_y"]
            isOneToOne: false
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          },
        ]
      }
      gene_patents: {
        Row: {
          description: string | null
          gene: string | null
          govt_id: string | null
          primary_key: number
          region: string | null
          response_date: string | null
          status: string | null
          submission_date: string | null
          type: string | null
        }
        Insert: {
          description?: string | null
          gene?: string | null
          govt_id?: string | null
          primary_key?: number
          region?: string | null
          response_date?: string | null
          status?: string | null
          submission_date?: string | null
          type?: string | null
        }
        Update: {
          description?: string | null
          gene?: string | null
          govt_id?: string | null
          primary_key?: number
          region?: string | null
          response_date?: string | null
          status?: string | null
          submission_date?: string | null
          type?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "gene_patents_gene_fkey"
            columns: ["gene"]
            isOneToOne: false
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          },
        ]
      }
      gene_progress_notes: {
        Row: {
          date: string | null
          description: string | null
          gene: string | null
          primary_key: number
        }
        Insert: {
          date?: string | null
          description?: string | null
          gene?: string | null
          primary_key?: number
        }
        Update: {
          date?: string | null
          description?: string | null
          gene?: string | null
          primary_key?: number
        }
        Relationships: [
          {
            foreignKeyName: "gene_progress_notes_gene_fkey"
            columns: ["gene"]
            isOneToOne: false
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          },
        ]
      }
      genes: {
        Row: {
          gene_id: string
          long_description: string | null
          ortho_group: string | null
          ortho_group_row_number: number | null
          reference_id: number | null
          short_description: string | null
          short_id: string | null
          standard_name: string | null
          symbol: string | null
        }
        Insert: {
          gene_id: string
          long_description?: string | null
          ortho_group?: string | null
          ortho_group_row_number?: number | null
          reference_id?: number | null
          short_description?: string | null
          short_id?: string | null
          standard_name?: string | null
          symbol?: string | null
        }
        Update: {
          gene_id?: string
          long_description?: string | null
          ortho_group?: string | null
          ortho_group_row_number?: number | null
          reference_id?: number | null
          short_description?: string | null
          short_id?: string | null
          standard_name?: string | null
          symbol?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "genes_reference_id_fkey"
            columns: ["reference_id"]
            isOneToOne: false
            referencedRelation: "assemblies"
            referencedColumns: ["id"]
          },
        ]
      }
      ortho_gene_id_map: {
        Row: {
          gene_ids: Json | null
          id: number
          ortho_group: string
        }
        Insert: {
          gene_ids?: Json | null
          id?: number
          ortho_group: string
        }
        Update: {
          gene_ids?: Json | null
          id?: number
          ortho_group?: string
        }
        Relationships: []
      }
      people: {
        Row: {
          email: string | null
          id: number
          name: string | null
        }
        Insert: {
          email?: string | null
          id?: number
          name?: string | null
        }
        Update: {
          email?: string | null
          id?: number
          name?: string | null
        }
        Relationships: []
      }
      phenotypers: {
        Row: {
          email: string | null
          first_name: string | null
          id: number
          last_name: string | null
          user_id: string | null
        }
        Insert: {
          email?: string | null
          first_name?: string | null
          id?: number
          last_name?: string | null
          user_id?: string | null
        }
        Update: {
          email?: string | null
          first_name?: string | null
          id?: number
          last_name?: string | null
          user_id?: string | null
        }
        Relationships: []
      }
      plate_plant_traits_list: {
        Row: {
          created_at: string
          id: number
          plant_trait: string | null
        }
        Insert: {
          created_at?: string
          id?: number
          plant_trait?: string | null
        }
        Update: {
          created_at?: string
          id?: number
          plant_trait?: string | null
        }
        Relationships: []
      }
      plates_exp: {
        Row: {
          blob_storage_path: string | null
          created_at: string
          experiment_name: string | null
          file_path: string | null
          genotype: string | null
          id: number
          plant_age: string | null
          planting_date: string | null
          plate_id: string | null
          pos_genotype_grid: Json | null
          scan_date: string | null
          scan_filename: string | null
          seedling_per_plate: number | null
          treatment: string | null
        }
        Insert: {
          blob_storage_path?: string | null
          created_at?: string
          experiment_name?: string | null
          file_path?: string | null
          genotype?: string | null
          id?: number
          plant_age?: string | null
          planting_date?: string | null
          plate_id?: string | null
          pos_genotype_grid?: Json | null
          scan_date?: string | null
          scan_filename?: string | null
          seedling_per_plate?: number | null
          treatment?: string | null
        }
        Update: {
          blob_storage_path?: string | null
          created_at?: string
          experiment_name?: string | null
          file_path?: string | null
          genotype?: string | null
          id?: number
          plant_age?: string | null
          planting_date?: string | null
          plate_id?: string | null
          pos_genotype_grid?: Json | null
          scan_date?: string | null
          scan_filename?: string | null
          seedling_per_plate?: number | null
          treatment?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "plates_exp_blob_storage_path_fkey"
            columns: ["blob_storage_path"]
            isOneToOne: false
            referencedRelation: "plates_source_table"
            referencedColumns: ["id"]
          },
        ]
      }
      plates_scan_trait: {
        Row: {
          id: number
          plant_id: string | null
          plate_id: number | null
          source_id: number | null
          trait_id: number | null
          value: number | null
        }
        Insert: {
          id?: number
          plant_id?: string | null
          plate_id?: number | null
          source_id?: number | null
          trait_id?: number | null
          value?: number | null
        }
        Update: {
          id?: number
          plant_id?: string | null
          plate_id?: number | null
          source_id?: number | null
          trait_id?: number | null
          value?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "plates_scan_trait_plate_id_fkey"
            columns: ["plate_id"]
            isOneToOne: false
            referencedRelation: "plates_exp"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "plates_scan_trait_source_id_fkey"
            columns: ["source_id"]
            isOneToOne: false
            referencedRelation: "plates_trait_source"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "plates_scan_trait_trait_id_fkey"
            columns: ["trait_id"]
            isOneToOne: false
            referencedRelation: "plate_plant_traits_list"
            referencedColumns: ["id"]
          },
        ]
      }
      plates_source_table: {
        Row: {
          created_at: string | null
          file_path: string | null
          id: string
        }
        Insert: {
          created_at?: string | null
          file_path?: string | null
          id?: string
        }
        Update: {
          created_at?: string | null
          file_path?: string | null
          id?: string
        }
        Relationships: []
      }
      plates_trait_source: {
        Row: {
          id: number
          source: string | null
        }
        Insert: {
          id?: number
          source?: string | null
        }
        Update: {
          id?: number
          source?: string | null
        }
        Relationships: []
      }
      rhizovision_accessions: {
        Row: {
          accession: string | null
          id: number
          type: string | null
        }
        Insert: {
          accession?: string | null
          id?: number
          type?: string | null
        }
        Update: {
          accession?: string | null
          id?: number
          type?: string | null
        }
        Relationships: []
      }
      rhizovision_experiments: {
        Row: {
          created_at: string | null
          experiment_name: string
          id: number
          scientist_email: string | null
          scientist_name: string | null
        }
        Insert: {
          created_at?: string | null
          experiment_name: string
          id?: number
          scientist_email?: string | null
          scientist_name?: string | null
        }
        Update: {
          created_at?: string | null
          experiment_name?: string
          id?: number
          scientist_email?: string | null
          scientist_name?: string | null
        }
        Relationships: []
      }
      rhizovision_images: {
        Row: {
          feature_image_path: string[] | null
          id: number
          plant_id: string
          segmentation_image_path: string[] | null
        }
        Insert: {
          feature_image_path?: string[] | null
          id?: number
          plant_id: string
          segmentation_image_path?: string[] | null
        }
        Update: {
          feature_image_path?: string[] | null
          id?: number
          plant_id?: string
          segmentation_image_path?: string[] | null
        }
        Relationships: []
      }
      rhizovision_settings: {
        Row: {
          id: number
          image_threshold_level: number | null
          pixel_to_mm_conversion: number | null
          root_pruning_threshold: number | null
          root_type: string
        }
        Insert: {
          id?: number
          image_threshold_level?: number | null
          pixel_to_mm_conversion?: number | null
          root_pruning_threshold?: number | null
          root_type: string
        }
        Update: {
          id?: number
          image_threshold_level?: number | null
          pixel_to_mm_conversion?: number | null
          root_pruning_threshold?: number | null
          root_type?: string
        }
        Relationships: []
      }
      scrna_cells: {
        Row: {
          barcode: string | null
          cell_number: number
          cluster_id: string | null
          dataset_id: number
          id: number
          x: number | null
          y: number | null
        }
        Insert: {
          barcode?: string | null
          cell_number: number
          cluster_id?: string | null
          dataset_id: number
          id?: number
          x?: number | null
          y?: number | null
        }
        Update: {
          barcode?: string | null
          cell_number?: number
          cluster_id?: string | null
          dataset_id?: number
          id?: number
          x?: number | null
          y?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "scrna_cells_dataset_id_fkey"
            columns: ["dataset_id"]
            isOneToOne: false
            referencedRelation: "scrna_datasets"
            referencedColumns: ["id"]
          },
        ]
      }
      scrna_counts: {
        Row: {
          counts_object_path: string | null
          dataset_id: number
          gene_id: number
          id: number
        }
        Insert: {
          counts_object_path?: string | null
          dataset_id: number
          gene_id: number
          id?: number
        }
        Update: {
          counts_object_path?: string | null
          dataset_id?: number
          gene_id?: number
          id?: number
        }
        Relationships: [
          {
            foreignKeyName: "scrna_counts_dataset_id_fkey"
            columns: ["dataset_id"]
            isOneToOne: false
            referencedRelation: "scrna_datasets"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "scrna_counts_gene_id_fkey"
            columns: ["gene_id"]
            isOneToOne: false
            referencedRelation: "scrna_genes"
            referencedColumns: ["id"]
          },
        ]
      }
      scrna_datasets: {
        Row: {
          annotation: string | null
          assembly: string | null
          id: number
          metadata: Json | null
          name: string
          scientist_id: number | null
          species_id: number
          strain: string | null
          url: string | null
        }
        Insert: {
          annotation?: string | null
          assembly?: string | null
          id?: number
          metadata?: Json | null
          name: string
          scientist_id?: number | null
          species_id: number
          strain?: string | null
          url?: string | null
        }
        Update: {
          annotation?: string | null
          assembly?: string | null
          id?: number
          metadata?: Json | null
          name?: string
          scientist_id?: number | null
          species_id?: number
          strain?: string | null
          url?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "scrna_datasets_scientist_id_fkey"
            columns: ["scientist_id"]
            isOneToOne: false
            referencedRelation: "people"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "scrna_datasets_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "cyl_plants_extended"
            referencedColumns: ["species_id"]
          },
          {
            foreignKeyName: "scrna_datasets_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "cyl_scans_extended"
            referencedColumns: ["species_id"]
          },
          {
            foreignKeyName: "scrna_datasets_species_id_fkey"
            columns: ["species_id"]
            isOneToOne: false
            referencedRelation: "species"
            referencedColumns: ["id"]
          },
        ]
      }
      scrna_de: {
        Row: {
          cluster_id: string | null
          dataset_id: number
          file_path: string
          id: number
        }
        Insert: {
          cluster_id?: string | null
          dataset_id: number
          file_path: string
          id?: number
        }
        Update: {
          cluster_id?: string | null
          dataset_id?: number
          file_path?: string
          id?: number
        }
        Relationships: [
          {
            foreignKeyName: "scrna_de_dataset_id_fkey"
            columns: ["dataset_id"]
            isOneToOne: false
            referencedRelation: "scrna_datasets"
            referencedColumns: ["id"]
          },
        ]
      }
      scrna_genes: {
        Row: {
          dataset_id: number
          gene_name: string
          gene_number: number
          id: number
        }
        Insert: {
          dataset_id: number
          gene_name: string
          gene_number: number
          id?: number
        }
        Update: {
          dataset_id?: number
          gene_name?: string
          gene_number?: number
          id?: number
        }
        Relationships: [
          {
            foreignKeyName: "scrna_genes_dataset_id_fkey"
            columns: ["dataset_id"]
            isOneToOne: false
            referencedRelation: "scrna_datasets"
            referencedColumns: ["id"]
          },
        ]
      }
      species: {
        Row: {
          common_name: string | null
          genus: string | null
          id: number
          illustration_path: string | null
          species: string | null
        }
        Insert: {
          common_name?: string | null
          genus?: string | null
          id?: number
          illustration_path?: string | null
          species?: string | null
        }
        Update: {
          common_name?: string | null
          genus?: string | null
          id?: number
          illustration_path?: string | null
          species?: string | null
        }
        Relationships: []
      }
      translation_candidates: {
        Row: {
          gene_candidate: string | null
          id: number
          translation_candidate: string | null
        }
        Insert: {
          gene_candidate?: string | null
          id?: number
          translation_candidate?: string | null
        }
        Update: {
          gene_candidate?: string | null
          id?: number
          translation_candidate?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "translation_candidates_gene_candidate_fkey"
            columns: ["gene_candidate"]
            isOneToOne: false
            referencedRelation: "gene_candidates"
            referencedColumns: ["gene"]
          },
          {
            foreignKeyName: "translation_candidates_translation_candidate_fkey"
            columns: ["translation_candidate"]
            isOneToOne: false
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          },
        ]
      }
      translation_lines: {
        Row: {
          accession_id: number | null
          gene_id: string | null
          id: number
          modification_description: string | null
          name: string | null
        }
        Insert: {
          accession_id?: number | null
          gene_id?: string | null
          id?: number
          modification_description?: string | null
          name?: string | null
        }
        Update: {
          accession_id?: number | null
          gene_id?: string | null
          id?: number
          modification_description?: string | null
          name?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "translation_lines_accession_id_fkey"
            columns: ["accession_id"]
            isOneToOne: false
            referencedRelation: "accessions"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "translation_lines_gene_id_fkey"
            columns: ["gene_id"]
            isOneToOne: false
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          },
        ]
      }
      translation_project_users: {
        Row: {
          id: number
          translation_project_id: number | null
          user_id: string | null
        }
        Insert: {
          id?: number
          translation_project_id?: number | null
          user_id?: string | null
        }
        Update: {
          id?: number
          translation_project_id?: number | null
          user_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "translation_project_users_translation_project_id_fkey"
            columns: ["translation_project_id"]
            isOneToOne: false
            referencedRelation: "translation_projects"
            referencedColumns: ["id"]
          },
        ]
      }
      translation_projects: {
        Row: {
          created_at: string
          id: number
          name: string
          spreadsheet_url: string | null
        }
        Insert: {
          created_at?: string
          id?: number
          name: string
          spreadsheet_url?: string | null
        }
        Update: {
          created_at?: string
          id?: number
          name?: string
          spreadsheet_url?: string | null
        }
        Relationships: []
      }
    }
    Views: {
      cyl_plants_extended: {
        Row: {
          accession_id: number | null
          experiment_description: string | null
          experiment_id: number | null
          experiment_name: string | null
          germ_day: number | null
          germ_day_color: string | null
          plant_id: number | null
          qr_code: string | null
          species_genus: string | null
          species_id: number | null
          species_name: string | null
          species_species: string | null
          wave_id: number | null
          wave_name: string | null
          wave_number: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_plants_accession_id_fkey"
            columns: ["accession_id"]
            isOneToOne: false
            referencedRelation: "accessions"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_scan_timeline: {
        Row: {
          count: number | null
          date_scanned: string | null
        }
        Relationships: []
      }
      cyl_scan_trait_names: {
        Row: {
          name: string | null
        }
        Insert: {
          name?: string | null
        }
        Update: {
          name?: string | null
        }
        Relationships: []
      }
      cyl_scans_extended: {
        Row: {
          accession_id: number | null
          date_scanned: string | null
          experiment_description: string | null
          experiment_id: number | null
          experiment_name: string | null
          germ_day: number | null
          germ_day_color: string | null
          phenotyper_id: number | null
          plant_age_days: number | null
          plant_id: number | null
          qr_code: string | null
          scan_id: number | null
          scanner_id: number | null
          species_genus: string | null
          species_id: number | null
          species_name: string | null
          species_species: string | null
          uploaded_at: string | null
          wave_id: number | null
          wave_name: string | null
          wave_number: number | null
        }
        Relationships: [
          {
            foreignKeyName: "cyl_plants_accession_id_fkey"
            columns: ["accession_id"]
            isOneToOne: false
            referencedRelation: "accessions"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_scans_phenotyper_id_fkey"
            columns: ["phenotyper_id"]
            isOneToOne: false
            referencedRelation: "phenotypers"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "cyl_scans_scanner_id_fkey"
            columns: ["scanner_id"]
            isOneToOne: false
            referencedRelation: "cyl_scanners"
            referencedColumns: ["id"]
          },
        ]
      }
      cyl_wave_timeline: {
        Row: {
          count: number | null
          date_scanned: string | null
          experiment_name: string | null
          species_name: string | null
          wave_number: number | null
        }
        Relationships: []
      }
    }
    Functions: {
      append_experiment_log: {
        Args: { gene_id: string; new_log: Json }
        Returns: undefined
      }
      assign_partition_numbers: { Args: never; Returns: undefined }
      create_cyl_dataset: {
        Args: {
          experiment_id: number
          name: string
          qc_set_name: Json
          timepoints: Json
          trait_source_id: number
        }
        Returns: undefined
      }
      dblink: { Args: { "": string }; Returns: Record<string, unknown>[] }
      dblink_cancel_query: { Args: { "": string }; Returns: string }
      dblink_close: { Args: { "": string }; Returns: string }
      dblink_connect: { Args: { "": string }; Returns: string }
      dblink_connect_u: { Args: { "": string }; Returns: string }
      dblink_current_query: { Args: never; Returns: string }
      dblink_disconnect:
        | { Args: never; Returns: string }
        | { Args: { "": string }; Returns: string }
      dblink_error_message: { Args: { "": string }; Returns: string }
      dblink_exec: { Args: { "": string }; Returns: string }
      dblink_fdw_validator: {
        Args: { catalog: unknown; options: string[] }
        Returns: undefined
      }
      dblink_get_connections: { Args: never; Returns: string[] }
      dblink_get_notify:
        | { Args: never; Returns: Record<string, unknown>[] }
        | { Args: { conname: string }; Returns: Record<string, unknown>[] }
      dblink_get_pkey: {
        Args: { "": string }
        Returns: Database["public"]["CompositeTypes"]["dblink_pkey_results"][]
        SetofOptions: {
          from: "*"
          to: "dblink_pkey_results"
          isOneToOne: false
          isSetofReturn: true
        }
      }
      dblink_get_result: {
        Args: { "": string }
        Returns: Record<string, unknown>[]
      }
      dblink_is_busy: { Args: { "": string }; Returns: number }
      get_scan_traits: {
        Args: { experiment_id_: number; trait_name_: string }
        Returns: {
          accession_name: string
          date_scanned: string
          germ_day: number
          plant_age_days: number
          plant_id: number
          plant_qr_code: string
          scan_id: number
          trait_name: string
          trait_value: number
          wave_number: number
        }[]
      }
      get_scans_without_videos: {
        Args: never
        Returns: {
          id: number
        }[]
      }
      get_unique_categories: {
        Args: never
        Returns: {
          category: string
        }[]
      }
      insert_cyl_qc_codes: { Args: { qc_codes: Json }; Returns: undefined }
      insert_image: {
        Args: {
          accession_name: string
          date_scanned_: string
          device_name: string
          experiment: string
          frame_number_: number
          germ_day: number
          germ_day_color: string
          plant_age_days: number
          plant_qr_code: string
          species_common_name: string
          wave_number: number
        }
        Returns: number
      }
      insert_image_v2_0: {
        Args: {
          accession_name: string
          date_scanned_: string
          device_name: string
          experiment: string
          frame_number_: number
          germ_day: number
          germ_day_color: string
          phenotyper_email: string
          phenotyper_name: string
          plant_age_days: number
          plant_qr_code: string
          scientist_email: string
          scientist_name: string
          species_common_name: string
          wave_number: number
        }
        Returns: number
      }
      insert_image_v3_0: {
        Args: {
          accession_name: string
          brightness: number
          contrast: number
          date_scanned_: string
          device_name: string
          experiment: string
          exposure_time: number
          frame_number_: number
          gain: number
          gamma: number
          germ_day: number
          germ_day_color: string
          num_frames: number
          phenotyper_email: string
          phenotyper_name: string
          plant_age_days: number
          plant_qr_code: string
          scientist_email: string
          scientist_name: string
          seconds_per_rot: number
          species_common_name: string
          wave_number: number
        }
        Returns: number
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      dblink_pkey_results: {
        position: number | null
        colname: string | null
      }
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const

