import * as React from 'react'
import { useState, useEffect } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import { Database } from '@/lib/database.types'
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'

interface Metadata {
  annotation: string
  assembly: string
  strain: string
  metadata: Record<string, any>
}

export default function ExpressionMetadata({ file_id }: { file_id: number }) {
  const supabase = createClientComponentClient<Database>()
  const [annotation, setAnnotation] = useState('NOT AVAILABLE')
  const [assembly, setAssembly] = useState('NOT AVAILABLE')
  const [strain, setStrain] = useState('NOT AVAILABLE')
  const [metadata, setMetadata] = useState({})

  useEffect(() => {
    const fetchData = async () => {
      const { data: metadata, error: metadataError }: { data: Metadata | null; error: any } =
        await supabase
          .from('scrna_datasets')
          .select('annotation, assembly, strain, metadata')
          .eq('id', file_id)
          .single()

      if (metadataError) {
        console.error('Error fetching data:', metadataError)
        return
      }

      if (metadata) {
        setAnnotation(metadata.annotation)
        setAssembly(metadata.assembly)
        setStrain(metadata.strain)
        setMetadata(metadata.metadata)
      }
    }

    if (file_id) {
      fetchData()
    }
  }, [file_id])

  return (
    <>
      <Box sx={{ display: 'flex', flexDirection: 'row', gap: 2, p: 2 }}>
        <Box
          sx={{
            flex: '1 1 40%',
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
            height: '200px',
          }}
        >
          <Typography variant="subtitle1" color="text.secondary">
            Assembly: {assembly}
          </Typography>
          <Typography variant="subtitle1" color="text.secondary">
            Annotation: {annotation}
          </Typography>
          <Typography variant="subtitle1" color="text.secondary">
            Strain: {strain}
          </Typography>
        </Box>

        <Box
          sx={{
            flex: '1 1 60%',
            height: '200px',
            display: 'flex',
            flexDirection: 'column',
            gap: 1,
          }}
        >
          <Typography variant="subtitle1" color="text.secondary">
            Metadata:
          </Typography>

          <Box
            sx={{
              flex: 1,
              overflowY: 'auto',
              padding: 0,
            }}
          >
            {metadata &&
              Object.entries(metadata).map(([key, value]) => (
                <Typography key={key} variant="subtitle1" color="text.secondary">
                  {key}: {String(value)}
                </Typography>
              ))}
          </Box>
        </Box>
      </Box>
    </>
  )
}
