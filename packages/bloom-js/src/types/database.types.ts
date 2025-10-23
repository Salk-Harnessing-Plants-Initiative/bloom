export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export interface Database {
  graphql_public: {
    Tables: {
      [_ in never]: never
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      graphql: {
        Args: {
          operationName?: string
          query?: string
          variables?: Json
          extensions?: Json
        }
        Returns: Json
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
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
          hpi_reference_id: string | null
          hpi_assembly: string | null
          id: number
          origin: string | null
          species_id: number | null
          version: number | null
        }
        Insert: {
          accession_name?: string | null
          archive_link: string
          external_link?: string | null
          external_version?: string | null
          hpi_reference_id?: string | null
          hpi_assembly: string | null
          id?: number
          origin?: string | null
          species_id?: number | null
          version?: number | null
        }
        Update: {
          accession_name?: string | null
          archive_link?: string
          external_link?: string | null
          external_version?: string | null
          hpi_reference_id?: string | null
          id?: number
          origin?: string | null
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
          }
        ]
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
          }
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
          }
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
          }
        ]
      }
      cyl_image_traits: {
        Row: {
          id: number
          image_id: number
          trait_id: number | null
          value: number | null
        }
        Insert: {
          id?: number
          image_id: number
          trait_id?: number | null
          value?: number | null
        }
        Update: {
          id?: number
          image_id?: number
          trait_id?: number | null
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
          {
            foreignKeyName: "cyl_image_traits_trait_id_fkey"
            columns: ["trait_id"]
            isOneToOne: false
            referencedRelation: "cyl_traits"
            referencedColumns: ["id"]
          }
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
          }
        ]
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
          }
        ]
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
          }
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
          }
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
          }
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
          }
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
          date_scanned: string | null
          id: number
          phenotyper_id: number | null
          plant_age_days: number | null
          plant_id: number | null
          scanner_id: number | null
          uploaded_at: string | null
        }
        Insert: {
          date_scanned?: string | null
          id?: number
          phenotyper_id?: number | null
          plant_age_days?: number | null
          plant_id?: number | null
          scanner_id?: number | null
          uploaded_at?: string | null
        }
        Update: {
          date_scanned?: string | null
          id?: number
          phenotyper_id?: number | null
          plant_age_days?: number | null
          plant_id?: number | null
          scanner_id?: number | null
          uploaded_at?: string | null
        }
        Relationships: [
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
          }
        ]
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
          }
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
          }
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
          }
        ]
      }
      gene_candidates: {
        Row: {
          category: string | null
          disclosed_to_otd: boolean | null
          evidence_description: string | null
          experiment_plans_and_progress: string | null
          gene: string
          publication_status: boolean | null
          status: string
          translation_approval_date: string | null
        }
        Insert: {
          category?: string | null
          disclosed_to_otd?: boolean | null
          evidence_description?: string | null
          experiment_plans_and_progress?: string | null
          gene: string
          publication_status?: boolean | null
          status?: string
          translation_approval_date?: string | null
        }
        Update: {
          category?: string | null
          disclosed_to_otd?: boolean | null
          evidence_description?: string | null
          experiment_plans_and_progress?: string | null
          gene?: string
          publication_status?: boolean | null
          status?: string
          translation_approval_date?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "gene_candidates_gene_fkey"
            columns: ["gene"]
            isOneToOne: true
            referencedRelation: "genes"
            referencedColumns: ["gene_id"]
          }
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
          }
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
          }
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
          }
        ]
      }
      genes: {
        Row: {
          gene_id: string
          long_description: string | null
          reference_id: number | null
          short_description: string | null
          short_id: string | null
          symbol: string | null
          ortho_group: string | null
          standard_name: string | null
          ortho_group_row_number : string | null
        }
        Insert: {
          gene_id: string
          long_description?: string | null
          reference_id?: number | null
          short_description?: string | null
          short_id?: string | null
          symbol?: string | null
          ortho_group: string | null
          standard_name: string | null
          ortho_group_row_number : string | null
        }
        Update: {
          gene_id?: string
          long_description?: string | null
          reference_id?: number | null
          short_description?: string | null
          short_id?: string | null
          symbol?: string | null
          ortho_group: string | null
          standard_name: string | null
          ortho_group_row_number : string | null
        }
        Relationships: [
          {
            foreignKeyName: "genes_reference_id_fkey"
            columns: ["reference_id"]
            isOneToOne: false
            referencedRelation: "assemblies"
            referencedColumns: ["id"]
          }
        ]
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
        Relationships: [
          {
            foreignKeyName: "phenotypers_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "users"
            referencedColumns: ["id"]
          }
        ]
      }
      scrna_cells: {
        Row: {
          barcode: string | null
          cell_number: number
          cluster_id: number | null
          dataset_id: number
          id: number
          x: number | null
          y: number | null
        }
        Insert: {
          barcode?: string | null
          cell_number: number
          cluster_id?: number | null
          dataset_id: number
          id?: number
          x?: number | null
          y?: number | null
        }
        Update: {
          barcode?: string | null
          cell_number?: number
          cluster_id?: number | null
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
          }
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
          }
        ]
      }
      scrna_datasets: {
        Row: {
          id: number
          name: string
          scientist_id: number | null
          species_id: number
          url: string | null
        }
        Insert: {
          id?: number
          name: string
          scientist_id?: number | null
          species_id: number
          url?: string | null
        }
        Update: {
          id?: number
          name?: string
          scientist_id?: number | null
          species_id?: number
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
          }
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
          }
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
          }
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
          }
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
          {
            foreignKeyName: "translation_project_users_user_id_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "users"
            referencedColumns: ["id"]
          }
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
          }
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
          }
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
      create_cyl_dataset: {
        Args: {
          name: string
          experiment_id: number
          trait_source_id: number
          qc_set_name: Json
          timepoints: Json
        }
        Returns: undefined
      }
      get_scan_traits: {
        Args: {
          experiment_id_: number
          trait_name_: string
        }
        Returns: {
          scan_id: number
          date_scanned: string
          plant_age_days: number
          wave_number: number
          plant_id: number
          germ_day: number
          plant_qr_code: string
          accession_name: string
          trait_name: string
          trait_value: number
        }[]
      }
      get_scans_without_videos: {
        Args: Record<PropertyKey, never>
        Returns: {
          id: number
        }[]
      }
      insert_cyl_qc_codes: {
        Args: {
          qc_codes: Json
        }
        Returns: undefined
      }
      insert_image: {
        Args: {
          species_common_name: string
          experiment: string
          wave_number: number
          germ_day: number
          germ_day_color: string
          plant_age_days: number
          date_scanned_: string
          device_name: string
          plant_qr_code: string
          accession_name: string
          frame_number_: number
        }
        Returns: number
      }
      insert_image_v2_0: {
        Args: {
          species_common_name: string
          experiment: string
          wave_number: number
          germ_day: number
          germ_day_color: string
          plant_age_days: number
          date_scanned_: string
          device_name: string
          plant_qr_code: string
          accession_name: string
          frame_number_: number
          phenotyper_name: string,
          phenotyper_email: string,
          scientist_name: string,
          scientist_email: string
        }
        Returns: number
      }
      append_experiment_log: {
        Args: {
          gene_id: string
          new_log: Json[]      
        }
        Returns: void      
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
  storage: {
    Tables: {
      buckets: {
        Row: {
          allowed_mime_types: string[] | null
          avif_autodetection: boolean | null
          created_at: string | null
          file_size_limit: number | null
          id: string
          name: string
          owner: string | null
          owner_id: string | null
          public: boolean | null
          updated_at: string | null
        }
        Insert: {
          allowed_mime_types?: string[] | null
          avif_autodetection?: boolean | null
          created_at?: string | null
          file_size_limit?: number | null
          id: string
          name: string
          owner?: string | null
          owner_id?: string | null
          public?: boolean | null
          updated_at?: string | null
        }
        Update: {
          allowed_mime_types?: string[] | null
          avif_autodetection?: boolean | null
          created_at?: string | null
          file_size_limit?: number | null
          id?: string
          name?: string
          owner?: string | null
          owner_id?: string | null
          public?: boolean | null
          updated_at?: string | null
        }
        Relationships: []
      }
      migrations: {
        Row: {
          executed_at: string | null
          hash: string
          id: number
          name: string
        }
        Insert: {
          executed_at?: string | null
          hash: string
          id: number
          name: string
        }
        Update: {
          executed_at?: string | null
          hash?: string
          id?: number
          name?: string
        }
        Relationships: []
      }
      objects: {
        Row: {
          bucket_id: string | null
          created_at: string | null
          id: string
          last_accessed_at: string | null
          metadata: Json | null
          name: string | null
          owner: string | null
          owner_id: string | null
          path_tokens: string[] | null
          updated_at: string | null
          version: string | null
        }
        Insert: {
          bucket_id?: string | null
          created_at?: string | null
          id?: string
          last_accessed_at?: string | null
          metadata?: Json | null
          name?: string | null
          owner?: string | null
          owner_id?: string | null
          path_tokens?: string[] | null
          updated_at?: string | null
          version?: string | null
        }
        Update: {
          bucket_id?: string | null
          created_at?: string | null
          id?: string
          last_accessed_at?: string | null
          metadata?: Json | null
          name?: string | null
          owner?: string | null
          owner_id?: string | null
          path_tokens?: string[] | null
          updated_at?: string | null
          version?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "objects_bucketId_fkey"
            columns: ["bucket_id"]
            isOneToOne: false
            referencedRelation: "buckets"
            referencedColumns: ["id"]
          }
        ]
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      can_insert_object: {
        Args: {
          bucketid: string
          name: string
          owner: string
          metadata: Json
        }
        Returns: undefined
      }
      extension: {
        Args: {
          name: string
        }
        Returns: string
      }
      filename: {
        Args: {
          name: string
        }
        Returns: string
      }
      foldername: {
        Args: {
          name: string
        }
        Returns: unknown
      }
      get_size_by_bucket: {
        Args: Record<PropertyKey, never>
        Returns: {
          size: number
          bucket_id: string
        }[]
      }
      search: {
        Args: {
          prefix: string
          bucketname: string
          limits?: number
          levels?: number
          offsets?: number
          search?: string
          sortcolumn?: string
          sortorder?: string
        }
        Returns: {
          name: string
          id: string
          updated_at: string
          created_at: string
          last_accessed_at: string
          metadata: Json
        }[]
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

export type Tables<
  PublicTableNameOrOptions extends
    | keyof (Database["public"]["Tables"] & Database["public"]["Views"])
    | { schema: keyof Database },
  TableName extends PublicTableNameOrOptions extends { schema: keyof Database }
    ? keyof (Database[PublicTableNameOrOptions["schema"]]["Tables"] &
        Database[PublicTableNameOrOptions["schema"]]["Views"])
    : never = never
> = PublicTableNameOrOptions extends { schema: keyof Database }
  ? (Database[PublicTableNameOrOptions["schema"]]["Tables"] &
      Database[PublicTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : PublicTableNameOrOptions extends keyof (Database["public"]["Tables"] &
      Database["public"]["Views"])
  ? (Database["public"]["Tables"] &
      Database["public"]["Views"])[PublicTableNameOrOptions] extends {
      Row: infer R
    }
    ? R
    : never
  : never

export type TablesInsert<
  PublicTableNameOrOptions extends
    | keyof Database["public"]["Tables"]
    | { schema: keyof Database },
  TableName extends PublicTableNameOrOptions extends { schema: keyof Database }
    ? keyof Database[PublicTableNameOrOptions["schema"]]["Tables"]
    : never = never
> = PublicTableNameOrOptions extends { schema: keyof Database }
  ? Database[PublicTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : PublicTableNameOrOptions extends keyof Database["public"]["Tables"]
  ? Database["public"]["Tables"][PublicTableNameOrOptions] extends {
      Insert: infer I
    }
    ? I
    : never
  : never

export type TablesUpdate<
  PublicTableNameOrOptions extends
    | keyof Database["public"]["Tables"]
    | { schema: keyof Database },
  TableName extends PublicTableNameOrOptions extends { schema: keyof Database }
    ? keyof Database[PublicTableNameOrOptions["schema"]]["Tables"]
    : never = never
> = PublicTableNameOrOptions extends { schema: keyof Database }
  ? Database[PublicTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : PublicTableNameOrOptions extends keyof Database["public"]["Tables"]
  ? Database["public"]["Tables"][PublicTableNameOrOptions] extends {
      Update: infer U
    }
    ? U
    : never
  : never

export type Enums<
  PublicEnumNameOrOptions extends
    | keyof Database["public"]["Enums"]
    | { schema: keyof Database },
  EnumName extends PublicEnumNameOrOptions extends { schema: keyof Database }
    ? keyof Database[PublicEnumNameOrOptions["schema"]]["Enums"]
    : never = never
> = PublicEnumNameOrOptions extends { schema: keyof Database }
  ? Database[PublicEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : PublicEnumNameOrOptions extends keyof Database["public"]["Enums"]
  ? Database["public"]["Enums"][PublicEnumNameOrOptions]
  : never

