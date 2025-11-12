'use client'
import * as React from 'react'
import { useState, useEffect } from 'react'
import { Database } from '@/lib/database.types'
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'
import ExpressionGeneLevelBoxPlot from './expression-genelevel-boxplot'
// import ExpressionGeneLevelScatterPlot from "./expression-gene-level-scatterplot";
import ExpressionMultiGeneDotPlot from './expression-multigene-dotplot'
import Autocomplete from '@mui/material/Autocomplete'
import TextField from '@mui/material/TextField'
import { data } from 'autoprefixer'
import { styled } from '@mui/material/styles'
import { Typography, Chip, Button } from '@mui/material'
import Tabs, { tabsClasses } from '@mui/material/Tabs'
import Tab from '@mui/material/Tab'
import Box from '@mui/material/Box'
import GeneDrillDown from './expression-gene-drilldown'
import CloseIcon from '@mui/icons-material/Close'

interface ExpressionGeneLevelProps {
  file_id: number
}

interface GeneClickEvent {
  gene: string
  index: number
}

interface Scatterplot {
  [key: string]: any
}

type GeneNames = {
  gene_number: number
  gene_name: string
}

type GeneData = {
  gene_id: number
  gene_name: string
  counts: [
    {
      key: number
      value: number
    },
  ]
  data:
    | {
        cluster_id: number | null
        barcode: string | null
        cell_number: number
        x: number | null
        y: number | null
      }[]
    | null
}

const StyledChip = styled(Chip)({
  margin: '2px',
  borderRadius: '20px',
  backgroundColor: '#ddd',
  cursor: 'pointer',
  transition: 'all 0.3s ease',
  '&:hover': {
    transform: 'translateY(-3px)',
    boxShadow: '0px 4px 10px rgba(0, 0, 255, 0.9)',
  },
})

export default function ExpressionGeneLevel({ file_id }: { file_id: number }) {
  //const [gene_list, setGeneList] = useState<GeneNames[]>([]);
  const [initial_load, setInitialLoad] = useState(false)
  const [selected_gene, setSelectedGene] = useState<{ gene_name: string; gene_number: number }>({
    gene_name: '',
    gene_number: 0,
  })
  const [value, setValue] = useState(0)
  const [drill_down_gene, setDrillDownGene] = useState<string | null>(null)
  // const [selected_gene, setSelectedGene] = useState<{ gene_name: string; gene_id: number }>({ gene_name: '', gene_id: 0 });
  const supabase = createClientComponentClient<Database>()
  const [isSelected_gene, setisSelectedGene] = useState<number[]>([])
  const [scatterplot, setScatterPlotData] = useState<Scatterplot>({})
  const [show_scatterplot, setShowScatterPlot] = useState(false)

  const [user_input, setInputValue] = useState('')
  const [add_gene, setAddGene] = useState<string | null>(null)
  const [input_array, setInputArray] = useState<Record<string, GeneData>>({})
  const [option, setOption] = useState<string[]>([])

  const handleshowplot = (value: boolean) => {
    setShowScatterPlot(value)
  }

  const handleChange = (event: React.SyntheticEvent, newValue: number) => {
    setValue(newValue)
  }

  const [tabs, setTabs] = useState([
    {
      label: 'Compare across Multiple Genes (Multiview)',
      content: (
        <>
          <div>Not Available</div>
        </>
      ),
    },
  ])

  const renderContent = () => {
    return <>{tabs[value]?.content}</>
  }

  const handleRemoveTab = (index: number) => {
    setTabs((prevTabs) => {
      const updatedTabs = prevTabs.filter((_, tabIndex) => tabIndex !== index)
      if (value === index && updatedTabs.length > 0) {
        setValue(Math.max(0))
      }
      return updatedTabs
    })
  }

  //Initial data Load
  useEffect(() => {
    const fetchData = async () => {
      try {
        const updatedInputArray: Record<string, GeneData> = {}
        const { data: geneData, error: geneError } = (await supabase
          .from('scrna_counts')
          .select('*')
          .eq('dataset_id', file_id)
          .limit(6)) as unknown as {
          data: Array<{ counts_object_path?: string; gene_id: number }>
          error: any
        }

        if (geneError) {
          console.error(`Error fetching data for gene`, geneError)
        }

        if (geneData) {
          for (let i = 0; i < geneData.length; i++) {
            if (!geneData[i].counts_object_path) {
              console.error(`No counts object path for gene`)
              continue
            }
            //cells
            const { data: storageData, error: storageError } = await supabase.storage
              .from('scrna')
              .download(geneData[i].counts_object_path!)

            if (storageError) {
              console.error(`Error fetching storage data for gene:`, storageError)
              continue
            }

            const { data: geneName, error: geneNameError } = await supabase
              .from('scrna_genes')
              .select('id, gene_number, gene_name')
              .eq('dataset_id', file_id)
              .eq('id', geneData[i].gene_id)

            if (geneNameError) {
              console.error(`Error fetching storage data for gene:`, storageError)
              continue
            }

            if (storageData) {
              const fileText = await storageData.text()
              const jsonData = JSON.parse(fileText)
              const cell_ids_list: number[] = jsonData.map(
                (cell: Record<string, number>) => Number(Object.keys(cell)[0]) - 1
              )

              const { data: clusterData, error: clusterError } = await supabase
                .from('scrna_cells')
                .select('cluster_id, barcode, cell_number, x, y')
                .eq('dataset_id', file_id)
                .in('cell_number', cell_ids_list)

              if (clusterError) {
                console.error(`Error fetching cluster data for gene`, clusterError)
                continue
              }
              const geneInfo = geneName[0] as { id: number; gene_number: number; gene_name: string }
              updatedInputArray[geneInfo.gene_name] = {
                gene_id: geneInfo.id,
                gene_name: geneInfo.gene_name,
                counts: jsonData,
                data: clusterData,
              }
            }
          }
        }
        setInputArray(updatedInputArray)
        setOption(Object.keys(updatedInputArray).map((gene) => gene))
      } catch (error) {
        console.error('Error in fetching data:', error)
      }
    }

    if (file_id) {
      ;(async () => {
        await fetchData()
        setInitialLoad(true)
      })()
    }
  }, [file_id])

  useEffect(() => {
    const gene_name = add_gene
    const fetchData = async () => {
      type GeneListRes = { id: number }
      const { data: gene_list_res, error: gene_list_error } = (await supabase
        .from('scrna_genes')
        .select('id')
        .eq('gene_name', gene_name || '')
        .eq('dataset_id', file_id)) as { data: GeneListRes[] | null; error: any }

      if (gene_list_res && gene_list_res.length > 0) {
        //Get all cells for a gene
        const { data: geneData, error: geneError } = (await supabase
          .from('scrna_counts')
          .select('*')
          .eq('gene_id', gene_list_res[0].id)
          .eq('dataset_id', file_id)) as unknown as {
          data: Array<{ counts_object_path?: string }>
          error: any
        }
        if (geneData && geneData[0] && geneData[0].counts_object_path) {
          const { data: storageData } = await supabase.storage
            .from('scrna')
            .download(geneData[0].counts_object_path)
          if (storageData) {
            //get the barcode and cluster id for the cells
            const fileText = await storageData.text()
            const jsonData = JSON.parse(fileText)
            const cell_ids_list = jsonData.map(
              (cell: { key: number; value: number }) => Number(Object.keys(cell)[0]) - 1
            )
            const { data: clusterData, error: clusterError } = await supabase
              .from('scrna_cells')
              .select('cluster_id, barcode, cell_number, x, y')
              .eq('dataset_id', file_id)
              .in('cell_number', cell_ids_list)
            setInputArray((prevValues) => ({
              ...prevValues,
              [gene_name || '']: {
                gene_id: gene_list_res[0].id,
                gene_name: gene_name || '',
                counts: jsonData,
                data: clusterData,
              },
            }))
          }
        }
      }
    }

    if (gene_name != null) {
      fetchData()
    }
  }, [add_gene])

  //TODO: add debounce
  useEffect(() => {
    const fetchData = async () => {
      try {
        const { data: gene_list_res, error: gene_list_error } = (await supabase
          .from('scrna_genes')
          .select('gene_number, gene_name')
          .eq('dataset_id', file_id)
          .ilike('gene_name', `%${user_input}%`)
          .limit(100)) as { data: { gene_number: number; gene_name: string }[] | null; error: any }
        if (gene_list_res && gene_list_res.length > 0) {
          setOption(gene_list_res.map((gene) => gene.gene_name))
        }
      } catch (error) {
        console.error('Error in fetching data:', error)
      }
    }
    if (user_input) {
      fetchData()
    }
  }, [user_input])

  useEffect(() => {
    if (!drill_down_gene) return

    setTabs((prevTabs) => {
      const newTabs = [
        ...prevTabs,
        {
          label: `Gene Level View : ${drill_down_gene}`,
          content: <GeneDrillDown key={drill_down_gene} geneData={input_array[drill_down_gene]} />,
        },
      ]

      setValue(newTabs.length - 1)

      return newTabs
    })

    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [drill_down_gene])

  return (
    <div>
      {file_id && initial_load && (
        <Autocomplete
          multiple
          limitTags={5}
          id="gene-selection-bar"
          options={option}
          loading={option.length === 0}
          getOptionLabel={(option) => option}
          defaultValue={option.slice(0, 6)}
          onInputChange={(_, newValue) => setInputValue(newValue)}
          onChange={(event, list, reason, detail) => {
            let gene_name = detail?.option || ''
            if (reason === 'removeOption') {
              setInputArray((prevValues) => {
                const newValues = { ...prevValues }
                if (detail) {
                  delete newValues[gene_name]
                }
                return newValues
              })
            }
            if (reason === 'selectOption') {
              setAddGene(gene_name)
            }
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              label="SELECT GENE"
              placeholder="Add genes for a comparative view."
            />
          )}
          renderTags={(value, getTagProps) =>
            value.map((option, index) => {
              const { key, ...tagProps } = getTagProps({ index })
              return (
                <StyledChip
                  key={option}
                  label={option}
                  {...tagProps}
                  onClick={() => {
                    setDrillDownGene(option)
                  }}
                />
              )
            })
          }
          sx={{ width: '100%' }}
        />
      )}
      {file_id && initial_load && (
        <Box
          sx={{
            flexGrow: 1,
          }}
        >
          <Tabs
            value={value}
            onChange={handleChange}
            variant="scrollable"
            scrollButtons
            aria-label="visible arrows tabs example"
            sx={{
              [`& .${tabsClasses.scrollButtons}`]: {
                '&.Mui-disabled': { opacity: 0.3 },
              },
            }}
          >
            {tabs.map((tab, index) => (
              <Tab
                key={index}
                label={
                  <div style={{ display: 'flex', alignItems: 'center' }}>
                    <span>{tab.label}</span>
                    {index !== 0 && (
                      <span onClick={() => handleRemoveTab(index)} style={{ marginLeft: '10px' }}>
                        <CloseIcon />
                      </span>
                    )}
                  </div>
                }
              />
            ))}
          </Tabs>
        </Box>
      )}
      {file_id && initial_load && (
        <div
          style={{
            display: 'flex',
            boxSizing: 'border-box',
            border: '1px solid rgb(62, 60, 60)',
            borderRadius: '5px',
            marginTop: '50px',
            flexDirection: 'column',
          }}
        >
          {value !== 0 && renderContent()}
          {value === 0 && (
            <>
              <Typography
                style={{ marginTop: '20px' }}
                align="center"
                variant="h6"
                fontWeight="bold"
                color="black"
              >
                Gene Expression Patterns Across Cell Clusters
              </Typography>
              <ExpressionMultiGeneDotPlot
                input_array={input_array}
                setDrillDownGene={setDrillDownGene}
              />
              ;
              <ExpressionGeneLevelBoxPlot
                input_array={input_array}
                gene_name={selected_gene.gene_name}
                gene_id={selected_gene.gene_number}
                file_id={file_id}
                setScatterPlotData={setScatterPlotData}
                handleshowplot={handleshowplot}
                setDrillDownGene={setDrillDownGene}
              />
            </>
          )}
        </div>
      )}
    </div>
  )
}
