import * as React from 'react';
import { useState, useEffect, useRef } from 'react';
import { Database } from "@/lib/database.types";

// Ensure the `qc_plot` column exists in the `scrna_datasets` table in your database schema.
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import * as d3 from "d3";

type ScrnaDataset = {
    qc_plot: string;
  };

export function RawCountsQC({ file_id, file_name }:{ file_id: number, file_name: string }) {
    const supabase = createClientComponentClient<Database>();
    const [imageUrl, setImageUrl] = useState<string | null>(null);

    // useEffect(() => {
        // const fetchData = async () => {
        //     const { data: qc_plot_path, error: qc_plot_error} = await supabase
        //         .from("scrna_datasets")
        //         .select("qc_plot")
        //         .eq("dataset_id", file_id)
        //         .limit(1);

        //     //TODO : update schema using migration script
        //     if (qc_plot_path && qc_plot_path.length > 0 && qc_plot_path[0].qc_plot) {
        //         const { data: storageData, error: storageError } = await supabase.storage
        //         .from("scrna")
        //         .download(qc_plot_path[0].qc_plot);
        //         if (storageError) {
        //             console.error("Error downloading image:", qc_plot_error);
        //             return;
        //         }
        //         if (qc_plot_path) {
        //             const url = URL.createObjectURL(qc_plot_path[0].qc_plot);
        //             setImageUrl(url);
        //         }
        //     }
        // };

    //     const fetchData = async () => {
    //         const { data: qc_plot_path, error: qc_plot_error } = await supabase
    //             .from("scrna_datasets")
    //             .select("qc_plot")
    //             .eq("dataset_id", file_id)
    //             .limit(1)
    //             .single();

    //         if (qc_plot_error) {
    //             console.error("Error fetching path from DB:", qc_plot_error);
    //             return;
    //         }

    //         if (qc_plot_path?.qc_plot) {
    //             const { data: storageData, error: storageError } = await supabase.storage
    //                 .from("scrna")
    //                 .download(qc_plot_path.qc_plot);

    //             if (storageError) {
    //                 console.error("Error downloading image:", storageError);
    //                 return;
    //             }

    //             if (storageData) {
    //                 const url = URL.createObjectURL(storageData);
    //                 setImageUrl(url);
    //             }
    //         }

    //     fetchData();
        
    // },[file_id+"_"+file_name]);

    return(
        <>
        <div>
            {imageUrl ? (
                <img src={imageUrl} alt="Raw Counts QC" style={{ maxWidth: '100%', height: 'auto' }} />
            ) : (
                <center><p>No data </p></center>
            )}
        </div>
        </>
    )
        
}