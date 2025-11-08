import React, { useEffect, useRef, useState } from 'react'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import FileDownloadIcon from '@mui/icons-material/FileDownload'
import html2canvas from 'html2canvas'
import CameraAltIcon from '@mui/icons-material/CameraAlt'
// import Menu from '@mui/material/Menu';
// import MenuItem from '@mui/material/MenuItem';
// import ListIcon from '@mui/icons-material/List';
import * as d3 from 'd3'
// import { Typography } from "@mui/material";

type GeneNames = {
  gene_number: number
  gene_name: string
}

type DotPlotData = {
  genes: string[]
  clusters: string[]
  expression: {
    gene: string
    cluster: string
    avg_value: number
    percent_expressed: number
    expressed_cells: number
    total_cells: number
  }[]
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

export default function ExpressionMultiGeneDotPlot({
  input_array,
  setDrillDownGene,
}: {
  input_array: Record<string, GeneData>
  setDrillDownGene: (gene_name: string) => void
}) {
  const chartRef = useRef<SVGSVGElement | null>(null)
  const colorLegendRef = useRef<HTMLDivElement | null>(null)
  const percentLegendRef = useRef<HTMLDivElement | null>(null)

  const [chartHeight, setChartHeight] = useState(100)
  const [data, setData] = useState<DotPlotData>({
    genes: [],
    clusters: [],
    expression: [],
  })
  const [anchorEl, setAnchorEl] = React.useState<null | HTMLElement>(null)
  const open = Boolean(anchorEl)
  const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    setAnchorEl(event.currentTarget)
  }
  const handleClose = () => {
    setAnchorEl(null)
  }
  const tooltipRef = useRef<HTMLDivElement | null>(null)
  const verticalOffset = 30

  const downloadJSON = () => {
    const dataStr = JSON.stringify(data, null, 2)
    const blob = new Blob([dataStr], { type: 'application/json' })
    const url = URL.createObjectURL(blob)

    const a = document.createElement('a')
    a.href = url
    a.download = 'expression_across_clusters.json'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const downloadCSV = () => {
    if (!data.genes.length || !data.clusters.length || !data.expression.length) return
    const headers = ['Gene', 'Cluster', 'Expression'].join(',') + '\n'
    const rows = data.genes
      .map((gene, index) => {
        return `"${gene}","${data.clusters[index]}",${JSON.stringify(data.expression[index])}`
      })
      .join('\n')
    const csvString = headers + rows
    const blob = new Blob([csvString], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'expression_across_clusters.csv'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const downloadChartAsPNG = () => {
    const divElement = document.getElementById('image-dot-plot')
    if (!divElement) return

    html2canvas(divElement, {
      allowTaint: true,
      useCORS: true,
      logging: true,
    })
      .then((canvas) => {
        const link = document.createElement('a')
        link.href = canvas.toDataURL('image/png')
        link.download = 'screenshot.png'
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
      })
      .catch((error) => {
        console.error('Error capturing screenshot:', error)
      })
  }

  useEffect(() => {
    const geneNames: any[] = Object.keys(input_array).map((gene_name: string) => ({
      gene_name: gene_name,
    }))
    const transformData = (input_array: Record<string, GeneData>): DotPlotData => {
      const genes: string[] = []
      const clusters: string[] = []
      const expression: {
        gene: string
        cluster: string
        avg_value: number
        percent_expressed: number
        expressed_cells: number
        total_cells: number
      }[] = []

      // 1: computing avg expression of gene across clusters
      // 2: % if cells expressed in each cluster.

      Object.values(input_array).forEach((geneData) => {
        const { gene_name, counts, data } = geneData
        if (!genes.includes(gene_name)) {
          genes.push(gene_name)
        }

        const geneClusterMap = new Map<
          string,
          { total: number; count: number; expressedCells: Set<string> }
        >()
        counts.forEach((item) => {
          Object.entries(item).forEach(([key, value]) => {
            const clusterData = data?.find((cell) => cell.cell_number === Number(key) - 1)
            const barcode = clusterData?.barcode

            if (clusterData?.cluster_id) {
              const clusterId = clusterData?.cluster_id.toString()
              if (!clusters.includes(clusterId)) {
                clusters.push(clusterId)
              }
              const mapKey = `${gene_name}@${clusterId}`
              if (!geneClusterMap.has(mapKey)) {
                geneClusterMap.set(mapKey, { total: 0, count: 0, expressedCells: new Set() })
              }
              const clusterStats = geneClusterMap.get(mapKey)!
              clusterStats.total += value
              clusterStats.count += 1
              if (value > 0 && clusterData.barcode)
                clusterStats.expressedCells.add(clusterData.barcode)
            }
          })
        })

        geneClusterMap.forEach((value, key) => {
          const [geneName, clusterId] = key.split('@')
          const avgExpression = value.total / value.count
          const percentExpressed = value.expressedCells.size / counts.length
          expression.push({
            gene: geneName,
            cluster: clusterId,
            avg_value: parseFloat(avgExpression.toFixed(2)),
            percent_expressed: parseFloat(percentExpressed.toFixed(2)),
            expressed_cells: value.expressedCells.size,
            total_cells: counts.length,
          })
        })
      })
      return {
        genes,
        clusters,
        expression,
      }
    }

    const dotPlotData = transformData(input_array)
    setData(dotPlotData)
  }, [input_array])

  useEffect(() => {
    if (!chartRef.current) return

    d3.select(chartRef.current).selectAll('*').remove()
    d3.select(colorLegendRef.current).selectAll('*').remove()
    d3.select(percentLegendRef.current).selectAll('*').remove()

    const container = chartRef.current.getBoundingClientRect()
    const margin = { top: 10, right: 60, bottom: 80, left: 70 }
    const width = container.width - margin.left - margin.right
    const height = container.height - margin.top - margin.bottom - 90

    const chartHeight = Math.min(data.genes.length * 100, 800)
    setChartHeight(chartHeight)
    const svg = d3
      .select(chartRef.current)
      .append('svg')
      .attr('width', width + margin.left + margin.right)
      .attr('height', chartHeight + margin.top + margin.bottom)
      .append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`)

    const x = d3.scaleBand().range([0, width]).domain(data.clusters).padding(0.05)
    svg
      .append('g')
      .attr('class', 'x-axis')
      .attr('transform', `translate(0, 20)`)
      .call(d3.axisBottom(x).tickSize(0))
      .select('.domain')
      .remove()

    svg.selectAll('.x-axis text').style('font-size', '16px').attr('transform', 'rotate(-45)')
    //svg.append("g").attr("class", "x-axis").call(d3.axisBottom(x).tickSize(0)).select(".domain").remove();

    svg
      .append('text')
      // .text(function(d){
      //     console.log(d)
      // })
      .attr('transform', `translate(${width / 2},10)`)
      .style('text-anchor', 'top')
      .style('font-size', '26px')
      .text('Cluster')

    const y = d3.scaleBand().range([0, chartHeight]).domain(data.genes).padding(0.05)
    svg
      .append('g')
      .attr('class', 'y-axis')
      .call(d3.axisLeft(y).tickSize(5))
      .select('.domain')
      .remove()

    svg
      .selectAll('.y-axis text')
      .style('text-anchor', 'middle')
      .style('transform', 'rotate(-60deg)')
      .style('transform-origin', 'middle center')
      .style('font-size', '14px')
      .style('cursor', 'pointer')
      .on('mouseover', function (event, d) {
        d3.select(this)
          .transition()
          .duration(200)
          .style('font-weight', 'bold')
          .style('fill', 'blue')
          .style('text-shadow', '0 0 10px rgba(0,0,255,0.8)')
      })
      .on('mouseout', function (event, d) {
        d3.select(this)
          .transition()
          .duration(200)
          .style('font-weight', 'normal')
          .style('fill', 'black')
          .style('text-shadow', 'none')
      })
      .on('click', function (event, d) {
        d3.select(this).style('fill', 'blue')
        setDrillDownGene(String(d))
      })

    svg
      .append('text')
      .attr('transform', `rotate(-90)`)
      .attr('y', -margin.left + 20)
      .attr('x', -chartHeight / 2)
      .style('text-anchor', 'middle')
      .style('font-size', '26px')
      .text('Genes')

    const myColor = d3
      .scaleSequential()
      .interpolator(d3.interpolatePurples)
      .domain([
        d3.min(data.expression, (d) => d.avg_value * 100) || 0,
        d3.max(data.expression, (d) => d.avg_value * 100) || 0,
      ])
    const radiusScale = d3.scaleLinear().domain([0, 100]).range([8, 40])

    const circles = svg
      .selectAll()
      .data(data.expression)
      .enter()
      .append('circle')
      .attr('cx', (d) => (x(d.cluster) ?? 0) + x.bandwidth() / 2 + 30)
      .attr('cy', (d) => (y(d.gene) ?? 0) + y.bandwidth() / 2 + 30)
      .attr('r', (d) => radiusScale(d.percent_expressed * 100))
      .style('fill', (d) => myColor(d.avg_value * 100))
      .style('opacity', 1)
      .on('mouseover', (event, d) => {
        if (tooltipRef.current) {
          tooltipRef.current.style.visibility = 'visible'
          tooltipRef.current.innerHTML = `Gene: ${d.gene}<br>Cluster: ${
            d.cluster
          }<br>Average Expression: ${d.avg_value} <br> Percentage: ${
            d.percent_expressed * 100
          }% <br> #cells/total: ${d.expressed_cells}/${d.total_cells} `
          tooltipRef.current.style.left = `${event.pageX + 10}px`
          tooltipRef.current.style.top = `${event.pageY + 10}px`
        }
      })
      .on('mouseout', () => {
        if (tooltipRef.current) {
          tooltipRef.current.style.visibility = 'hidden'
        }
      })

    const colorLegend = d3
      .select(colorLegendRef.current)
      .append('svg')
      .attr('width', 150)
      .attr('height', 80)
    const gradient = colorLegend
      .append('defs')
      .append('linearGradient')
      .attr('id', 'legend-gradient')
      .attr('x1', '0%')
      .attr('x2', '100%')
      .attr('y1', '0%')
      .attr('y2', '0%')
    gradient
      .selectAll('stop')
      .data([
        { offset: '0%', color: myColor(myColor.domain()[0]) },
        { offset: '100%', color: myColor(myColor.domain()[1]) },
      ])
      .enter()
      .append('stop')
      .attr('offset', (d) => d.offset)
      .attr('stop-color', (d) => d.color)
    colorLegend
      .append('rect')
      .attr('x', 0)
      .attr('y', 25)
      .attr('width', 150)
      .attr('height', 10)
      .style('fill', 'url(#legend-gradient)')
    colorLegend
      .append('text')
      .attr('x', 0)
      .attr('y', 50)
      .style('font-size', '18px')
      .text(Math.floor(d3.min(data.expression, (d) => d.avg_value) ?? 0).toFixed(1))
    colorLegend
      .append('text')
      .attr('x', 150)
      .attr('y', 50)
      .style('font-size', '18px')
      .style('text-anchor', 'end')
      .text(Math.ceil(d3.max(data.expression, (d) => d.avg_value) ?? 0).toFixed(1))

    const sizeLegend = d3
      .select(percentLegendRef.current)
      .append('svg')
      .attr('width', 150)
      .attr('height', 150)
    sizeLegend
      .append('text')
      .attr('x', 0)
      .attr('y', -10)
      .style('font-size', '14px')
      .text('Point Size (Percentage)')
    sizeLegend
      .append('circle')
      .attr('cx', 20)
      .attr('cy', 30)
      .attr('r', radiusScale(20))
      .style('fill', 'gray')
    sizeLegend
      .append('circle')
      .attr('cx', 60)
      .attr('cy', 30)
      .attr('r', radiusScale(30))
      .style('fill', 'gray')
    sizeLegend
      .append('circle')
      .attr('cx', 120)
      .attr('cy', 30)
      .attr('r', radiusScale(60))
      .style('fill', 'gray')
    sizeLegend
      .append('text')
      .attr('x', 15)
      .attr('y', 70)
      .style('font-size', '12px')
      .text(20 + '%')
    sizeLegend
      .append('text')
      .attr('x', 55)
      .attr('y', 70)
      .style('font-size', '12px')
      .text(30 + '%')
    sizeLegend
      .append('text')
      .attr('x', 115)
      .attr('y', 70)
      .style('font-size', '12px')
      .text(60 + '%')

    function sortByExpression() {
      const svg = d3.select(chartRef.current)
      const rects = svg.selectAll('rect')

      const sortedGenes = [...data.genes].sort((a, b) => {
        const aValue = data.expression.find((d) => d.gene === a)?.avg_value || 0
        const bValue = data.expression.find((d) => d.gene === b)?.avg_value || 0
        return bValue - aValue
      })

      y.domain(sortedGenes)

      svg
        .select('.y-axis')
        .transition()
        .duration(1000)
        .attr('transform', `translate(0, ${verticalOffset})`)
        .call(d3.axisLeft(y) as any)

      circles
        .transition()
        .duration(1000)
        .attr('cy', (d) => (y(d.gene) ?? 0) + y.bandwidth() / 2 + 30)

      // circles.transition()
      //     .duration(1000)
      //     .attr("y", function(d: any) { return (y(d.gene) ?? 0) + verticalOffset; });

      svg
        .select('.x-axis')
        .transition()
        .duration(1000)
        .call(d3.axisBottom(x) as any)
    }

    function sortByGeneName() {
      const sortedGenes = [...data.genes].sort((a, b) => a.localeCompare(b))

      y.domain(sortedGenes)

      svg
        .select('.y-axis')
        .transition()
        .duration(1000)
        .attr('transform', `translate(0, ${verticalOffset})`)
        .call(d3.axisLeft(y) as any)

      // circles.transition()
      //     .duration(1000)
      //     .attr("y", d => (y(d.gene) ?? 0) + verticalOffset);

      circles
        .transition()
        .duration(1000)
        .attr('cy', (d) => (y(d.gene) ?? 0) + y.bandwidth() / 2 + 30)

      svg
        .select('.x-axis')
        .transition()
        .duration(1000)
        .call(d3.axisBottom(x) as any)
    }

    d3.select('#sortByCounts').on('click', sortByExpression)
    d3.select('#sortByGenes').on('click', sortByGeneName)
  }, [data])

  return (
    <>
      {/* <div style={{ alignItems: 'right', display: 'flex', justifyContent: 'right', padding: '10px' }}>
                <Button
                    id="basic-button"
                    aria-controls={open ? 'basic-menu' : undefined}
                    aria-haspopup="true"
                    aria-expanded={open ? 'true' : undefined}
                    onClick={handleClick}
                >
                    FILTER <ListIcon />
                </Button>
                <Menu
                    id="basic-menu"
                    anchorEl={anchorEl}
                    open={open}
                    onClose={handleClose}
                >
                    <MenuItem onClick={() => { handleClose }}>
                        <button id="sortByCounts" >
                            Sort by Expression (E)
                        </button>
                    </MenuItem>
                    <MenuItem onClick={handleClose}>
                        <button id="sortByGenes" >
                            Sort by gene (E)
                        </button>
                    </MenuItem>
                    <MenuItem onClick={handleClose}>CELL TYPE</MenuItem>
                </Menu>
            </div> */}
      <Box sx={{ padding: '18px' }}>
        <Button
          onClick={() => {
            downloadJSON()
          }}
          variant="outlined"
          style={{ marginRight: '10px' }}
        >
          JSON <FileDownloadIcon />
        </Button>
        <Button
          variant="outlined"
          onClick={() => {
            downloadCSV()
          }}
          style={{ marginRight: '10px' }}
        >
          CSV <FileDownloadIcon />
        </Button>
        <Button
          variant="outlined"
          onClick={() => {
            downloadChartAsPNG()
          }}
          style={{ marginRight: '10px' }}
        >
          <CameraAltIcon /> <FileDownloadIcon />
        </Button>
      </Box>
      <Box id="image-dot-plot">
        <div
          ref={tooltipRef}
          style={{
            position: 'absolute',
            visibility: 'hidden',
            backgroundColor: 'rgba(0, 0, 0, 0.7)',
            color: 'white',
            padding: '5px',
            borderRadius: '5px',
            fontSize: '12px',
            pointerEvents: 'none',
            zIndex: 10,
          }}
        ></div>
        <div style={{ display: 'flex', flexDirection: 'row', justifyContent: 'center' }}>
          <div
            ref={colorLegendRef}
            id="colorLegend"
            style={{ marginRight: '10px', fontSize: '16px' }}
          >
            Avg. Expression Levels{' '}
          </div>
          <div
            ref={percentLegendRef}
            id="percentLegend"
            style={{ marginRight: '10px', fontSize: '16px' }}
          >
            {' '}
            % of Cells Expressing the Gene{' '}
          </div>
        </div>
        <div style={{ flex: '1', display: 'flex', height: 'auto', minHeight: chartHeight }}>
          {/* <button id="sortByCounts" >
                    Sort by Expression (E)
            </button>
            <button id="sortByGenes" >
                    Sort by gene (E)
            </button> */}
          <svg
            style={{ flex: '1', overflowY: 'auto', justifyContent: 'center', alignItems: 'center' }}
            ref={chartRef}
            height={chartHeight + 40}
          ></svg>
        </div>
      </Box>
    </>
  )
}
