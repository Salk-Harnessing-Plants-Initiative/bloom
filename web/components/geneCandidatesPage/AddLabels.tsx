import { useState } from 'react'
import Box from '@mui/material/Box'
import Typography from '@mui/material/Typography'
import Popover from '@mui/material/Popover'
import * as GeneTypes from '../../types/genecandidates'

import { Checkbox, FormControlLabel } from '@mui/material'

export default function AddLabels({
  anchorEl,
  handleCloseTagPopover,
  setSelectedTags,
  selectedTags,
}: {
  anchorEl: null | HTMLElement
  handleCloseTagPopover: () => void
  setSelectedTags: React.Dispatch<React.SetStateAction<GeneTypes.Tag[]>>
  selectedTags: GeneTypes.Tag[]
}) {
  // const [selectedTags, setSelectedTags] = useState<Tag[]>([]);
  const [availableTags, setAvailableTags] = useState<GeneTypes.Tag[]>([
    { color: '#87cbe6', label: 'Administrative' },
    { color: '#87e6af', label: 'Publication' },
    { color: '#d791e3', label: 'Data' },
    { color: '#f7c77f', label: 'Biochemistry' },
    { color: '#9be2e8', label: 'Orthology' },
    { color: '#f28fb5', label: 'Validation' },
    { color: '#b0e87c', label: 'Discovery' },
    { color: '#e6b77f', label: 'Translation' },
  ])

  const open = Boolean(anchorEl)
  const id = open ? 'tag-popover' : undefined

  return (
    <>
      <Popover
        id={id}
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={handleCloseTagPopover}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
        PaperProps={{
          sx: {
            width: 240,
            borderRadius: 2,
            boxShadow: 3,
            // backdropFilter: 'blur(6px)',
            backgroundColor: '#f1f5f9',
          },
        }}
      >
        <Box sx={{ p: 2 }}>
          <Typography
            variant="subtitle2"
            sx={{
              mb: 1,
              px: 1,
              py: 0.5,
              backgroundColor: '#f1f5f9',
              borderRadius: 1,
              fontWeight: 500,
              fontSize: 13,
              textAlign: 'center',
            }}
          >
            Select Tags
          </Typography>

          <Box
            sx={{
              maxHeight: 280,
              overflowY: 'auto',
              pr: 1,
              mt: 1,
            }}
          >
            {availableTags.map((tag) => (
              <Box
                key={tag.label}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  width: '100%',
                  py: 0.5,
                  gap: 1,
                  '&:hover': {
                    backgroundColor: '#e2e8f0',
                    borderRadius: 1,
                  },
                }}
              >
                <Checkbox
                  size="small"
                  checked={selectedTags.includes(tag)}
                  onChange={(e) => {
                    const checked = e.target.checked
                    setSelectedTags((prev: GeneTypes.Tag[]) =>
                      checked
                        ? [...prev, tag]
                        : prev.filter((t: GeneTypes.Tag) => t.label !== tag.label)
                    )
                  }}
                />
                <Box
                  sx={{
                    flexGrow: 1,
                    px: 1,
                    py: 0.5,
                    borderRadius: 1,
                    fontSize: 12,
                    backgroundColor: tag.color,
                    color: '#fff',
                    width: '100%',
                    textAlign: 'center',
                    border: '1px solid rgba(0, 0, 0, 0.3)',
                    boxShadow: '0 1px 3px rgba(0, 0, 0, 0.8)',
                    textShadow: '1px 1px 2px rgba(0, 0, 0, 1.9)',
                  }}
                >
                  {tag.label}
                </Box>
              </Box>
            ))}
          </Box>

          <Typography variant="caption" color="text.secondary" sx={{ mt: 5, my: 5 }}>
            <strong>Need a new label category?</strong> Request it via email:
            <br />
            (nhartwick@salk.edu, eberrigan@salk.edu, bfernando@salk.edu)
          </Typography>
        </Box>
      </Popover>
    </>
  )
}
