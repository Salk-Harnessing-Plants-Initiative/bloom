import React, { useEffect, useState, useRef } from 'react'
// import { corr } from 'mathjs'
import * as d3 from 'd3'
import { Box, Typography, Checkbox, FormGroup, FormControlLabel } from '@mui/material'
import { Database } from '@/lib/database.types'
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'
// import { sampleRankCorrelation } from "simple-statistics";
import Button from '@mui/material/Button'
import Popover from '@mui/material/Popover'
import ListIcon from '@mui/icons-material/List'

const geneList = [
  'Gene1',
  'Gene2',
  'Gene3',
  'Gene4',
  'Gene5',
  'Gene6',
  'Gene7',
  'Gene8',
  'Gene9',
  'Gene10',
  'Gene11',
  'Gene12',
  'Gene13',
  'Gene14',
  'Gene15',
  'Gene16',
  'Gene17',
  'Gene18',
  'Gene19',
  'Gene20',
  'Gene21',
  'Gene22',
  'Gene23',
  'Gene24',
  'Gene25',
  'Gene26',
  'Gene27',
  'Gene28',
  'Gene29',
  'Gene30',
  'Gene31',
  'Gene32',
  'Gene33',
  'Gene34',
  'Gene35',
  'Gene36',
  'Gene37',
  'Gene38',
  'Gene39',
  'Gene40',
]

type GeneData = { [key: string]: { [key: number]: number } }

type GeneList = { id: number; gene_name: string }[]

export default function CorrelationAnalysis({ file_id }: { file_id: number }) {
  const [selectedGenes, setSelectedGenes] = useState<string[]>([])
  const chartRef = useRef<SVGSVGElement | null>(null)
  const [geneData, setGeneData] = useState<GeneData>({})
  const [geneNames, setGeneNames] = useState<GeneList>()
  const [open, setOpen] = useState(false)
  const supabase = createClientComponentClient<Database>()
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const { data: gene_list_res, error: gene_list_error } = await supabase
          .from('scrna_genes')
          .select('id, gene_name')
          .eq('dataset_id', file_id)
        // const { data: gene_list_res, error: gene_list_error } = await supabase.rpc("get_gene_names", { file_id });
        if (gene_list_res) {
          setGeneNames(gene_list_res)
        }
      } catch (error) {
        console.error('Error in fetching data:', error)
      }
    }

    if (file_id) {
      fetchData()
    }
  }, [file_id])

  const toggleDrawer = (newOpen: boolean, event: React.MouseEvent<HTMLElement>) => {
    setOpen(newOpen)
    setAnchorEl(event.currentTarget)
  }

  const handleClose = (close: boolean) => {
    setOpen(close)
    setAnchorEl(null)
  }

  type ScrnaCount = {
    id: number
    gene_id: number
    dataset_id: number
    counts_object_path: string
    // add other fields if needed
  }

  const fetchCountsData = async (gene_id: number, gene_name: string) => {
    try {
      const { data: geneData, error: geneError } = (await supabase
        .from('scrna_counts')
        .select('*')
        .eq('gene_id', gene_id)
        .eq('dataset_id', file_id)) as { data: ScrnaCount[] | null; error: any }

      if (geneData?.[0]?.counts_object_path) {
        const { data: storageData } = await supabase.storage
          .from('scrna')
          .download(geneData[0].counts_object_path)

        if (storageData) {
          const fileText = await storageData.text()
          const jsonData = JSON.parse(fileText)
          const formattedGeneData = jsonData.reduce(
            (
              acc: {
                [gene: string]: { [key: number]: number }
                allKeys: number[]
              },
              current: { [key: string]: number }
            ) => {
              const key = Object.keys(current)[0]
              const value = current[key]

              if (!acc[gene_name]) {
                acc[gene_name] = {}
              }
              acc[gene_name][Number(key)] = value
              acc.allKeys.push(Number(key))
              return acc
            },
            { allKeys: [] } as {
              [gene: string]: { [key: number]: number }
              allKeys: number[]
            }
          )

          setGeneData((prevVal) => ({ ...prevVal, [gene_name]: formattedGeneData[gene_name] }))
          return true
        }
      }

      return false
    } catch (error) {
      console.error('Error in fetching data:', error)
      return false
    }
  }

  const handleGeneSelection = async (gene_id: number, gene_name: string) => {
    try {
      const geneData = await fetchCountsData(gene_id, gene_name)
      if (geneData) {
        setSelectedGenes((prev) =>
          prev.includes(gene_name) ? prev.filter((g) => g !== gene_name) : [...prev, gene_name]
        )
      }
    } catch (error) {
      console.error('Error fetching gene data:', error)
    }
  }

  // const calculatePearsonCorrelation = (xValues: number[], yValues: number[]) => {
  //     return corr(xValues, yValues);
  // };

  const FilterOptions = (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={() => handleClose(false)}
      sx={{ marginRight: '50px' }}
      anchorOrigin={{
        vertical: 'top',
        horizontal: 'left',
      }}
      transformOrigin={{
        vertical: 'top',
        horizontal: 'left',
      }}
    >
      <div style={{ padding: '10px', minWidth: '180px' }}>
        <h4 style={{ margin: '0 0 10px 0' }}>Filter by Cell Type</h4>
        <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          <li>
            <Button>🌱 Cell Type 1</Button>
          </li>
          <li>
            <Button>🌿 Cell Type 2</Button>
          </li>
          <li>
            <Button>🌾 Cell Type 3</Button>
          </li>
          <li>
            <Button>🌰 Cell Type 4</Button>
          </li>
          <li>
            <Button>🌱 Cell Type 5</Button>
          </li>
          <li>
            <Button>🍃 Cell Type 6</Button>
          </li>
          <li>
            <Button>🪵 Cell Type 7</Button>
          </li>
        </ul>
      </div>
    </Popover>
  )

  function downloadCSV(x: number[], y: number[], filename = 'gene_counts.csv') {
    const csvContent =
      'data:text/csv;charset=utf-8,' +
      'GeneX,GeneY\n' +
      x.map((val, i) => `${val},${y[i]}`).join('\n')

    const encodedUri = encodeURI(csvContent)
    const link = document.createElement('a')
    link.setAttribute('href', encodedUri)
    link.setAttribute('download', filename)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const drawScatterPlot = (genes: string[]) => {
    if (!chartRef.current) return
    d3.select(chartRef.current).selectAll('*').remove()

    const numGenes = genes.length
    const container = chartRef.current.getBoundingClientRect()

    const margin = { top: 20, right: 20, bottom: 20, left: 20 }
    const sizeWholeWidth = container.width - margin.left - margin.right
    const sizeWholeHeight = container.height - margin.top - margin.bottom
    const size = (sizeWholeWidth - 30 * numGenes) / numGenes

    const svg = d3
      .select(chartRef.current)
      .attr('width', sizeWholeWidth)
      .attr('height', sizeWholeHeight)

    const position = d3
      .scalePoint()
      .domain(genes)
      .range([0, sizeWholeWidth - size])

    // const positionY = d3.scalePoint()
    //     .domain(genes)
    //     .range([0, sizeWholeHeight - size]);

    const tooltip = d3.select('#tooltip')

    for (let i = 0; i < numGenes; i++) {
      for (let j = 0; j < numGenes; j++) {
        if (j <= i) continue
        if (i === j) continue

        let geneX = genes[i]
        let geneY = genes[j]

        const geneXValues = Object.values(geneData[geneX] as { [key: number]: number }).map((val) =>
          Number(val !== undefined ? val : 0)
        )
        const geneYValues = Object.values(geneData[geneY] as { [key: number]: number }).map((val) =>
          Number(val !== undefined ? val : 0)
        )

        let x = d3
          .scaleLinear()
          .domain([0, d3.max(geneXValues) as number])
          .range([0, size - 20])
        let y = d3
          .scaleLinear()
          .domain([0, d3.max(geneYValues) as number])
          .range([size, 0])

        let cell = svg
          .append('g')
          .attr(
            'transform',
            `translate(${(position(geneX.toString()) ?? 0) + 50 + 40 * i}, ${
              position(geneY.toString()) ?? 0
            })`
          )
        //.attr("transform", `translate(${(positionX(geneX.toString()) ?? 0) + 50 + (20 * i)}, ${(positionY(geneY.toString()) ?? 0) + 10 * j})`);

        cell.append('g').attr('transform', `translate(0,${size})`).call(d3.axisBottom(x).ticks(3))

        cell
          .append('text')
          .attr('transform', `translate(${size / 2},${size + 25})`)
          .style('text-anchor', 'middle')
          .text(`${geneX}`)

        cell.append('g').call(d3.axisLeft(y).ticks(3))

        cell
          .append('text')
          .attr('transform', `rotate(-90)`)
          .attr('y', -20)
          .attr('x', -size / 2)
          .style('text-anchor', 'middle')
          .text(`${geneY}`)

        const barcodesX = Object.keys(geneData[geneX])
        const barcodesY = Object.keys(geneData[geneY])
        const commonBarcodes = [...new Set([...barcodesX, ...barcodesY])]
        let geneXCounts: number[] = []
        let geneYCounts: number[] = []

        cell
          .selectAll('circle')
          .data(commonBarcodes)
          .enter()
          .append('circle')
          .attr('cx', (barcode) => {
            const value =
              (geneData[geneX] as { [key: string]: number })[barcode] !== undefined
                ? (geneData[geneX] as { [key: string]: number })[barcode]
                : 0
            geneXCounts.push(value)
            return x(value)
          })
          .attr('cy', (barcode) => {
            const value =
              (geneData[geneY] as { [key: string]: number })[barcode] !== undefined
                ? (geneData[geneY] as { [key: string]: number })[barcode]
                : 0
            geneYCounts.push(value)
            return y(value)
          })
          .attr('r', 4)
          .attr('fill', '#69b3a2')
          .on('mouseover', function (event, barcode) {
            const valueX =
              (geneData[geneX] as { [key: string]: number })[barcode] !== undefined
                ? (geneData[geneX] as { [key: string]: number })[barcode]
                : 0
            const valueY =
              (geneData[geneY] as { [key: string]: number })[barcode] !== undefined
                ? (geneData[geneY] as { [key: string]: number })[barcode]
                : 0
            tooltip
              .style('visibility', 'visible')
              .text(`${barcode} : (${valueX},${valueY})`)
              .style('top', event.pageY + 5 + 'px')
              .style('left', event.pageX + 5 + 'px')
          })
          .on('mouseout', function () {
            tooltip.style('visibility', 'hidden')
          })

        // const correlation = sampleRankCorrelation(geneXCounts, geneYCounts);
        // downloadCSV(geneXCounts, geneYCounts);
        // console.log(geneXCounts);
        // console.log(geneYCounts);

        cell
          .append('text')
          .attr('transform', `translate(${size / 2},${size / 2})`)
          .style('text-anchor', 'middle')
          .style('font-size', '12px')
          .style('fill', '#000')
        // .text(`r: ${Number(correlation).toFixed(2)}`);
      }
    }

    // for (let i = 0; i < numGenes; i++) {
    //     for (let j = 0; j < numGenes; j++) {
    //         if (i != j) { continue; }
    //             let geneX = genes[i];
    //             let geneY = genes[j];

    //             svg
    //             .append('g')
    //             .attr("transform", "translate(" + position(geneX) + "," + position(geneY) + ")")
    //             .append('text')
    //               .attr("x", size/2)
    //               .attr("y", size/2)
    //               .text(geneX)
    //               .attr("text-anchor", "middle")

    //     }
    // }
  }

  useEffect(() => {
    drawScatterPlot(selectedGenes)
  }, [selectedGenes])

  return (
    <>
      <Box display="flex" height="100vh">
        {/* Gene Selection */}
        <Box display="flex" flexDirection={'column'} sx={{ width: '15%' }}>
          <Typography variant="h6" sx={{ textAlign: 'center' }}>
            Select Genes
          </Typography>
          <Box
            sx={{
              width: '100%',
              height: '100vh',
              overflow: 'auto',
              mt: 2,
              border: '1px solid #ddd',
              borderRadius: 2,
              p: 1,
            }}
          >
            <FormGroup>
              {geneNames &&
                geneNames.map((gene) => (
                  <FormControlLabel
                    key={gene.id}
                    control={
                      <Checkbox
                        checked={selectedGenes.includes(gene.gene_name)}
                        onChange={() => handleGeneSelection(gene.id, gene.gene_name)}
                      />
                    }
                    label={gene.gene_name}
                  />
                ))}
            </FormGroup>
          </Box>
        </Box>
        {/* Plot Area */}
        <Box width="85%" p={0} sx={{ display: 'flex', alignItems: 'flex-start' }}>
          <div
            id="tooltip"
            style={{
              position: 'absolute',
              visibility: 'hidden',
              backgroundColor: '#c0c2c0',
              borderRadius: '8px',
              boxShadow: '2px 2px 5px rgba(0, 0, 0, 0.3)',
            }}
          ></div>
          <svg
            style={{
              width: '100vh',
              height: '100vh',
            }}
            ref={chartRef}
          ></svg>
          {/* <Box sx={{ display: "flex", flexDirection: "column", marginTop: '5rem' }}>
                        <Button onClick={(event) => toggleDrawer(true, event)}>FILTER<ListIcon /></Button>
                        {open && (<Box sx={{ width: "100%", marginTop: "3rem" }}>
                            {FilterOptions}
                        </Box>)}
                    </Box> */}
        </Box>
      </Box>
    </>
  )
}
