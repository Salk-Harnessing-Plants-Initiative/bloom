import { useState } from 'react'
import * as TypeDefs from '../../types/genecandidates'
import { Box, Button, Popover, TextField, Autocomplete, Typography } from '@mui/material'

export default function AddNewCategory({
  setCategory,
  categories,
}: {
  setCategory: React.Dispatch<React.SetStateAction<TypeDefs.Category[]>>
  categories: TypeDefs.Category[]
}) {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null)
  const [newCategory, setNewCategory] = useState('')
  const [message, setMessage] = useState<string | null>(null)

  const handleClick = (event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget)
  }

  const handleClose = () => {
    setAnchorEl(null)
    setNewCategory('')
    setMessage(null)
  }

  const handleAddCategory = () => {
    if (newCategory) {
      setCategory([...categories, { category: newCategory }])
    }
    setMessage('Successfully added new category!')
  }

  const open = Boolean(anchorEl)
  const id = open ? 'add-category-popover' : undefined

  return (
    <Box sx={{ height: '90%', p: 0 }}>
      <Button variant="outlined" onClick={handleClick}>
        Add New Category
      </Button>

      <Popover
        id={id}
        open={open}
        anchorEl={anchorEl}
        onClose={handleClose}
        anchorOrigin={{
          vertical: 'bottom',
          horizontal: 'left',
        }}
      >
        <Box sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 1 }}>
          <TextField
            label="New Category"
            value={newCategory}
            onChange={(e) => setNewCategory(e.target.value)}
            size="small"
          />

          <Button
            variant="contained"
            sx={{
              color: 'white',
              backgroundColor: '#1976d2 !important',
              '&:hover': {
                backgroundColor: '#1565c0',
              },
            }}
            onClick={handleAddCategory}
          >
            Add
          </Button>

          {message && <Typography variant="subtitle2">{message}</Typography>}
        </Box>
      </Popover>
    </Box>
  )
}
