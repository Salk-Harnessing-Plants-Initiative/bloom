import FileDownloadIcon from '@mui/icons-material/FileDownload'
import Button from '@mui/material/Button'
import { use, useEffect, useState } from 'react'
import { Database } from '@/lib/database.types'
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'
import { Box, Typography } from '@mui/material'
import LinearProgress, { LinearProgressProps } from '@mui/material/LinearProgress'

function LinearProgressWithLabel(props: LinearProgressProps & { value: number }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center' }}>
      <Box sx={{ width: '100%', mr: 1 }}>
        <LinearProgress variant="determinate" {...props} />
      </Box>
      <Box sx={{ minWidth: 35 }}>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>{`${Math.round(
          props.value
        )}%`}</Typography>
      </Box>
    </Box>
  )
}

export default function ExpressonDownloadFiles({
  file_id,
  file_name,
}: {
  file_id: number
  file_name: string
}) {
  const [trigger_download, setTrigger] = useState(false)
  const supabase = createClientComponentClient<Database>()
  const [progress, setProgress] = useState(0)
  const [progress_genelist, setProgressGeneList] = useState(0)
  const [progress_barcodes, setProgressBarcodes] = useState(0)
  // const [buffer, setBuffer] = useState(1000);
  const [total_count, setCount] = useState<number>(1)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const fetchData = async () => {
      try {
        setLoading(true)
        let from = 0
        const batchSize = 1000
        let processedCount = 0

        const { data, error, count } = await supabase
          .from('scrna_counts')
          .select('*', { count: 'exact' })
          .eq('dataset_id', file_id)
        setCount(count || 1)

        let hasMoreData = true
        const blobParts = []

        while (hasMoreData) {
          const { data: countsPath, error: geneError } = await supabase
            .from('scrna_counts')
            .select('*')
            .eq('dataset_id', file_id)
            .range(from, from + batchSize - 1)

          if (geneError) {
            console.error('Error fetching counts data:', geneError)
            break
          }

          if (!countsPath || countsPath.length === 0) {
            hasMoreData = false
            break
          }

          const batchData = []

          for (const count of countsPath as any[]) {
            if (!count.counts_object_path) {
              console.error('Invalid path: counts_object_path is null or undefined')
              continue
            }

            const { data: storageData, error: storageError } = await supabase.storage
              .from('scrna')
              .download(count.counts_object_path)

            if (storageError) {
              console.error('Error downloading file from S3:', storageError)
              continue
            }

            const fileData = await storageData.text()
            const parsedData = JSON.parse(fileData)
            batchData.push({ [count.gene_id]: parsedData })
          }

          const batchJson = JSON.stringify(batchData) + ','
          blobParts.push(new Blob([batchJson], { type: 'application/json' }))

          console.log(
            `Processed ${batchData.length} records, Total Blob Parts: ${blobParts.length}`
          )
          if (count) {
            setProgress((processedCount / count) * 100)
            // console.log("Percentage completed:"+(processedCount / count) * 100);
          }
          processedCount += batchSize
          from += batchSize
        }

        const finalBlob = new Blob(['[', ...blobParts, ']'], { type: 'application/json' })
        const a = document.createElement('a')
        a.href = URL.createObjectURL(finalBlob)
        a.download = 'file_name_normalized_counts.json'
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(a.href)
      } catch (error) {
        console.error('Error in fetching data:', error)
      } finally {
        setTrigger(false)
      }
    }

    const fetchGeneNames = async () => {
      try {
        let from = 0
        const batchSize = 1000
        let processedCount = 0
        let hasMoreData = true
        const blobParts: BlobPart[] = ['[']

        const { data, error, count } = await supabase
          .from('scrna_genes')
          .select('*', { count: 'exact' })
          .eq('dataset_id', file_id)

        while (hasMoreData) {
          const { data: geneList, error: geneError } = await supabase
            .from('scrna_genes')
            .select('gene_number, gene_name')
            .eq('dataset_id', file_id)
            .range(from, from + batchSize - 1)

          if (geneError) {
            console.error('Error fetching counts data:', geneError)
            break
          }

          if (!geneList || geneList.length === 0) {
            hasMoreData = false
            break
          }

          const batchJson = JSON.stringify(geneList)
          blobParts.push(batchJson, ',')

          if (count) {
            setProgressGeneList(((processedCount + geneList.length) / count) * 100)
          }
          processedCount += geneList.length
          from += batchSize
        }

        if (blobParts.length > 1) {
          blobParts.pop()
        }
        blobParts.push(']')

        const blob = new Blob(blobParts, { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = 'gene_list.json'
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      } catch (error) {
        console.error('Error in fetching data:', error)
      }
    }

    const fetchBarcodes = async () => {
      try {
        let from = 0
        const batchSize = 1000
        let processedCount = 0
        let hasMoreData = true
        const blobParts: BlobPart[] = ['[']

        const { data, error, count } = await supabase
          .from('scrna_cells')
          .select('*', { count: 'exact' })
          .eq('dataset_id', file_id)

        while (hasMoreData) {
          const { data: barcodes, error: barcodeError } = await supabase
            .from('scrna_cells')
            .select('cell_number, barcode')
            .eq('dataset_id', file_id)
            .range(from, from + batchSize - 1)

          if (barcodeError) {
            console.error('Error fetching counts data:', barcodeError)
            break
          }

          if (!barcodes || barcodes.length === 0) {
            hasMoreData = false
            break
          }

          const batchJson = JSON.stringify(barcodes)
          blobParts.push(batchJson, ',')

          if (count) {
            setProgressBarcodes(((processedCount + barcodes.length) / count) * 100)
          }
          processedCount += barcodes.length
          from += batchSize
        }

        if (blobParts.length > 1) {
          blobParts.pop()
        }
        blobParts.push(']')

        const blob = new Blob(blobParts, { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = 'barcode_list.json'
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      } catch (error) {
        console.error('Error in fetching data:', error)
      }
    }

    if (trigger_download) {
      Promise.all([fetchData(), fetchGeneNames(), fetchBarcodes()])
        // .then(() => console.log("All downloads started in parallel"))
        .catch((error) => console.error('Error in parallel downloads:', error))
    }
  }, [trigger_download])

  return (
    <>
      <div style={{ padding: '20px' }}>
        <h1 style={{ marginBottom: '10px', fontWeight: 'bold' }}>Download Dataset:</h1>
        <Box>
          <Box style={{ width: '100%' }}>
            {'Normalized Counts, Barcodes, GeneList'}
            <Button
              variant="outlined"
              style={{ marginRight: '10px', marginLeft: '10px' }}
              onClick={() => setTrigger((prevState) => !prevState)}
            >
              JSON <FileDownloadIcon />
            </Button>
          </Box>
          {loading && (
            <>
              <Box sx={{ width: '100%' }}>
                <Box sx={{ display: 'flex', alignItems: 'center' }}>
                  <Box sx={{ flex: '0 0 1', minWidth: '100px' }}>
                    <Typography>Normalized Counts Data : </Typography>
                  </Box>
                  <Box sx={{ flex: '1', margin: '40px' }}>
                    <LinearProgressWithLabel value={progress} />
                  </Box>
                </Box>
              </Box>
              <Box sx={{ width: '100%' }}>
                <Box sx={{ display: 'flex', alignItems: 'center' }}>
                  <Box sx={{ flex: '0 0 1', minWidth: '100px' }}>
                    <Typography>GeneList : </Typography>
                  </Box>
                  <Box sx={{ flex: '1', margin: '40px' }}>
                    <LinearProgressWithLabel value={progress_genelist} />
                  </Box>
                </Box>
              </Box>
              <Box sx={{ width: '100%' }}>
                <Box sx={{ display: 'flex', alignItems: 'center' }}>
                  <Box sx={{ flex: '0 0 1', minWidth: '100px' }}>
                    <Typography>Cells : </Typography>
                  </Box>
                  <Box sx={{ flex: '1', margin: '40px' }}>
                    <LinearProgressWithLabel value={progress_barcodes} />
                  </Box>
                </Box>
              </Box>
            </>
          )}
        </Box>
        {/* <ul style={{ listStyle: "none", padding: 0 }}>
                    {[
                        "Normalized Counts", "GeneList", "Barcodes",
                    ].map((item, index) => (
                        <li
                            key={index}
                            style={{
                                display: "flex",
                                alignItems: "center",
                                marginBottom: "10px"
                            }}
                        >
                            <span style={{ minWidth: "180px" }}>{item}</span>
                            <div>
                                <Button
                                    variant="outlined"
                                    style={{ marginRight: "10px", marginLeft:"10px"}}
                                    onClick={() => setTrigger((prevState) => !prevState)}
                                >
                                    JSON <FileDownloadIcon />
                                </Button>
                                {/* <Button
                                    variant="outlined"
                                    onClick={() => { }}
                                >
                                    CSV <FileDownloadIcon />
                                </Button>
                            </div>
                            <Box sx={{ width: '100%', marginLeft:"20px", marginRight:"20px" }}>
                               {loading &&

                                <LinearProgressWithLabel value={progress} />
                                 }
                            </Box>
                        </li>
                    ))}
                </ul> */}
      </div>
    </>
  )
}
