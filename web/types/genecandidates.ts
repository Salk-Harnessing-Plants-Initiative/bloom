/* Select Tag Component */

export type Tag = {
    label: string;
    color: string;
};

/* Progress Component */

export type Logs = {
    gene: string;
    timestamp: string | Date;
    user_email: string;
    message: string;
    images: string [];
    links: { url: string, text: string | null }[];
    tags: Tag[];
}

/* Add New Gene Candidate Section */

export type SpeciesList = {
    id  : number;
    accession_name: string| null; 
    hpi_assembly: string | null;
    origin : string | null;
    species: { common_name: string | null } | null
    species_id: number | null;
}

export type Category ={
    category: string | null;
}

export type People = {
    id  : number;
    email: string | null;
    name: string | null;
}

export type Status =
  | "suspected"
  | "under-investigation"
  | "stopped"
  | "in-translation"
  | "translation-confirmed";


