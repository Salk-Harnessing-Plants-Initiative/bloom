"use client";
import * as React from 'react';
import { useState , useEffect} from 'react';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import InputLabel from '@mui/material/InputLabel';
import MenuItem from '@mui/material/MenuItem';
import FormControl from '@mui/material/FormControl';
import Select, { SelectChangeEvent } from '@mui/material/Select';
import ExportScatterPlot from './expression-scatterplot';
import ExpressionGeneLevel from './expression-genelevel';
import DifferentialExpressionAnalysis from './expression-differential-analysis';
import ExpressionCorrelation from './expression-correlation-section';
import BasicTabs from './expression-correlation-page';
import ExpressonDownloadFiles from './expression-download-files';
import Tabs from '@mui/material/Tabs';
import Tab from '@mui/material/Tab';
import { Database } from "@/lib/database.types";
import { createClientComponentClient } from "@supabase/auth-helpers-nextjs";
import ExpressionMetadata  from './expression-metadata';
import { RawCountsQC } from './expression-qc-raw-reads';


export interface ExpressionPageProps {

    specieslist: {

        id: number;

        name: string;

        scientist_id: number | null;

        species_id: number;

        url: string | null;

        people: {

            email: string | null;

            id: number;

            name: string | null;

        } | null;

    }[];
}

interface TabPanelProps {
    children?: React.ReactNode;
    index: number;
    value: number;
}

function CustomTabPanel(props: TabPanelProps) {
    const { children, value, index, ...other } = props;

    return (
        <div
            role="tabpanel"
            hidden={value !== index}
            id={`simple-tabpanel-${index}`}
            aria-labelledby={`simple-tab-${index}`}
            {...other}
        >
            {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
        </div>
    );
}

function a11yProps(index: number) {
    return {
        id: `simple-tab-${index}`,
        'aria-controls': `simple-tabpanel-${index}`,
    };
}

export default function ExpressionPage({ specieslist }: ExpressionPageProps) {
    
    const supabase = createClientComponentClient<Database>();
    const [file_id, setFileid] = useState(0)
    const [file_name, setFileName] = useState('')
    const [slected_val, setSelectedVal] = useState({ file_id: specieslist[0]?.id || null, file_name:  specieslist[0]?.name || null })
    const [results, setResults] = useState(false);
    const [value, setValue] = useState(0);

    const selectfilename = (event: SelectChangeEvent<any>) => {
        const file = JSON.parse(event.target.value)
        setFileid(Number(file.id));
        setFileName(file.name);
        setResults((prevState) => !prevState);
        setSelectedVal(event.target.value);
    }

    const handleChange = (_event: React.SyntheticEvent, newValue: number) => {
        setValue(newValue);
    };
      

    return (
        <div>
            <Box sx={{ minWidth: 120, marginTop: "20px", padding: "50px", }}>
                <FormControl fullWidth>
                    <InputLabel id="demo-simple-select-label">Select Dataset</InputLabel>
                    <Select
                        labelId="main-view-select-label"
                        id="main-view-select"
                        value={slected_val}
                        label="Select Dataset"
                        onChange={selectfilename}
                    >
                        {specieslist.map((file, index) => (
                            <MenuItem key={index} value={JSON.stringify(file)}>{file.id} {file.name}</MenuItem>
                        ))}
                    </Select>
                </FormControl>
            </Box>

            {file_id > 0 && <ExpressionMetadata file_id={file_id}/>}

            <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
                <Tabs value={value} onChange={handleChange} aria-label="basic tabs example">
                    <Tab label="QC and Raw Counts Info" {...a11yProps(0)} />
                    <Tab label="UMAP" {...a11yProps(1)} />
                    <Tab label="Gene Expression Explorer" {...a11yProps(2)} />
                    <Tab label="Correlation" {...a11yProps(3)} />
                    <Tab label="Differential Expression" {...a11yProps(4)} />
                    <Tab label="Download Files" {...a11yProps(5)} />
                </Tabs>
            </Box>

            <CustomTabPanel value={value} index={0}>
                {results && < RawCountsQC file_id={file_id} file_name={file_name} />}
            </CustomTabPanel> 
            <CustomTabPanel value={value} index={1}>
                {results && <ExportScatterPlot file_id={file_id} file_name={file_name} />}
            </CustomTabPanel>
            <CustomTabPanel value={value} index={2}>
                {results && <ExpressionGeneLevel file_id={file_id} />}
            </CustomTabPanel>
            <CustomTabPanel value={value} index={3}>
                {results && <BasicTabs file_id={file_id} />}
            </CustomTabPanel> 
            <CustomTabPanel value={value} index={4}>
                {results && <DifferentialExpressionAnalysis file_id={file_id} />}
            </CustomTabPanel>
            <CustomTabPanel value={value} index={5}>
                {results && <ExpressonDownloadFiles file_id={file_id} file_name={file_name} />}
            </CustomTabPanel> 

            {/* {results && (
                <>
                    {/* <ExpressonDownloadFiles file_id={file_id} file_name={file_name} />
                    <ExportScatterPlot file_id={file_id} file_name={file_name} />
                    <ExpressionGeneLevel file_id={file_id} /> 
                    <ExpressionCorrelation file_id={file_id} />
                </>
            )}  */}

        </div>
    )

}