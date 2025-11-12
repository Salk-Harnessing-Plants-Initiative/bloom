'use client'

import {
  //   createServerSupabaseClient,
  getUser,
} from '@salk-hpi/bloom-nextjs-auth'
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'

import './styles.css'

import Mixpanel from 'mixpanel'
import { DataGrid, GridColDef } from '@mui/x-data-grid'
import Paper from '@mui/material/Paper'
import type { Database } from '@/lib/database.types'
import { useEffect, useState, Fragment, useRef } from 'react'
import Button from '@mui/material/Button'
import Typography from '@mui/material/Typography'
import Modal from '@mui/material/Modal'
import Box from '@mui/material/Box'
import CloseIcon from '@mui/icons-material/Close'
import TextField from '@mui/material/TextField'
import Alert from '@mui/material/Alert'
import { CircularProgress } from '@mui/material'
import SettingsIcon from '@mui/icons-material/Settings'
import Dialog from '@mui/material/Dialog'
import DialogActions from '@mui/material/DialogActions'
import DialogContent from '@mui/material/DialogContent'
import DialogContentText from '@mui/material/DialogContentText'
import DialogTitle from '@mui/material/DialogTitle'
import AddIcon from '@mui/icons-material/Add'
import _, { get } from 'lodash'
import Drawer from '@mui/material/Drawer'
import List from '@mui/material/List'
import ListItem from '@mui/material/ListItem'
import ListItemIcon from '@mui/material/ListItemIcon'
import ListItemText from '@mui/material/ListItemText'
import IconButton from '@mui/material/IconButton'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import LabelIcon from '@mui/icons-material/Label'
import DashboardIcon from '@mui/icons-material/Dashboard'
import Tooltip from '@mui/material/Tooltip'
import SearchIcon from '@mui/icons-material/Search'
import Autocomplete from '@mui/material/Autocomplete'
import SendIcon from '@mui/icons-material/Send'
import InsertLinkIcon from '@mui/icons-material/InsertLink'
import AddPhotoAlternateIcon from '@mui/icons-material/AddPhotoAlternate'
import FileDownloadIcon from '@mui/icons-material/FileDownload'
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks'
import AddGeneCandidateModal from '../../../components/geneCandidatesPage/AddGeneCandidateModal'
import Progress from '../../../components/geneCandidatesPage/Progress'
import CurrentStatus from '../../../components/geneCandidatesPage/CurrentStatusUpdate'

import { SupabaseClient } from '@supabase/supabase-js'

import {
  MenuItem,
  Checkbox,
  FormControlLabel,
  Select,
  InputLabel,
  FormControl,
  SelectChangeEvent,
} from '@mui/material'

type PersonRow = Database['public']['Tables']['people']['Row']
// type GenesTableRow = Database["public"]["Tables"]["genes"]["Update"];

type GeneRow = {
  category: string | null
  disclosed_to_otd: boolean | null
  evidence_description: string | null
  experiment_plans_and_progress: string | null
  status: string
  gene: string
  publication_status: boolean | null
  translation_approval_date: string | null
  people: PersonRow[]
  genes: {
    short_id: string | null
    symbol: string | null
    assemblies: {
      hpi_reference_id: string | null
    } | null
    standard_name: string | null
  } | null
}

type AssemblyData = {
  id: number
  prefix: string | null
}

type OrthoGroupData = {
  ortho_group: string | null
  gene_id: string
  short_id: string | null
  assemblies: { id: number; prefix: string | null }
  ortho_group_row_number: number | null
}

interface FormContentProps {
  ortho_group: OrthoGroupData[] | null
  nick_name: string
  selected_gene: string
  closeModalBox: () => void
}

type GeneData = {
  gene_id: string
  ortho_group: string | null
}

type LookupOptions = {
  gene_id: string
  standard_name: string
  ortho_group: string
}

type LookupGeneData = {
  gene_id: string
  ortho_group: string | null
  standard_name: string | null
  assemblies: {
    hpi_reference_id: string | null
    prefix: string | null
  }
}

type OrthoGroupGeneList = {
  gene_id: string
  ortho_group: string
  standard_name: string
  symbol: string
  assemblies: {
    hpi_reference_id: string | null
    prefix: string | null
  }
}

type UsersProgress = {
  date: string
  user: string
  message: string
}

function AlertDialog({
  openDialouge,
  sample_name,
  handleSubmitForm,
}: {
  openDialouge: boolean
  sample_name: String
  handleSubmitForm: () => void
}) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    setOpen(openDialouge)
  }, [openDialouge])

  const handleClickOpen = () => {
    setOpen(true)
  }

  const handleClose = () => {
    setOpen(false)
  }

  const handleSubmit = () => {
    setOpen(false)
    handleSubmitForm()
  }

  return (
    <Fragment>
      <Dialog
        open={open}
        onClose={handleClose}
        aria-labelledby="alert-dialog-title"
        aria-describedby="alert-dialog-description"
      >
        <DialogTitle id="alert-dialog-title">
          {'Please Check the standard name before submitting!'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText id="alert-dialog-description">{sample_name}</DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose}>Disagree</Button>
          <Button onClick={handleSubmit} autoFocus>
            Agree
          </Button>
        </DialogActions>
      </Dialog>
    </Fragment>
  )
}

function FormContent({ ortho_group, nick_name, closeModalBox, selected_gene }: FormContentProps) {
  // const supabase = createClientComponentClient<Database>();
  const supabase = createClientComponentClient<Database>() as unknown as SupabaseClient<Database>
  const [loading, setLoading] = useState(false)
  const [openDialouge, setOpenDialouge] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const checkBeforeSubmit = () => {
    setOpenDialouge(true)
  }

  const handleFormSubmit = async () => {
    if (!ortho_group || ortho_group.length === 0) {
      setMessage('Error: Cannot perform the action!')
      return
    }
    setLoading(true)
    try {
      for (const gene of ortho_group) {
        const updatePayload = {
          standard_name: String(
            `${gene.assemblies?.prefix ?? 'undefined'}-${nick_name ?? 'undefined'}-${
              gene.ortho_group_row_number ?? null
            }`
          ),
          ortho_group: String(gene.ortho_group) ?? null,
          ortho_group_row_number: gene.ortho_group_row_number ?? null,
        }

        const { error } = await supabase
          .from('genes')
          .update(updatePayload)
          .eq('gene_id', gene.gene_id)

        if (error) {
          console.error(`Error updating gene ${gene.gene_id}:`, error)
        }
      }
    } catch (err) {
      console.error('Unexpected error:', err)
    } finally {
      setLoading(false)
      closeModalBox()
    }
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        p: 2,
        justifyContent: 'center',
        alignItems: 'center',
        width: '100%',
      }}
    >
      {message && (
        <Alert
          severity="error"
          action={<CloseIcon onClick={() => setMessage(null)} style={{ cursor: 'pointer' }} />}
        >
          {message}
        </Alert>
      )}

      <Typography variant="h6">Ortho Genes</Typography>
      <Box sx={{ display: 'flex', flexDirection: 'row', gap: 2, width: '100%' }}>
        <Box
          sx={{
            flex: 1,
            border: '1px solid gray',
            p: 2,
            height: '200px',
            overflow: 'scroll',
            borderRadius: '5px',
          }}
        >
          <Typography variant="h6" align="center">
            Gene Names
          </Typography>

          {ortho_group &&
            ortho_group.map((gene) => {
              return (
                <Box
                  key={gene.gene_id}
                  sx={{ display: 'flex', flexDirection: 'row', gap: 2, width: '100%' }}
                >
                  <Typography align="center">{gene.gene_id}</Typography>
                </Box>
              )
            })}
        </Box>

        <Box sx={{ flex: 0.2, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Typography variant="h6">→</Typography>
        </Box>

        <Box
          sx={{
            flex: 1,
            border: '1px solid gray',
            p: 2,
            height: '200px',
            overflow: 'scroll',
            borderRadius: '5px',
          }}
        >
          <Typography variant="h6" align="center">
            Standard Names
          </Typography>

          {ortho_group &&
            ortho_group.map((gene) => {
              return (
                <Box
                  key={gene.gene_id}
                  sx={{ display: 'flex', flexDirection: 'row', gap: 2, width: '100%' }}
                >
                  <Typography align="center">
                    {gene.assemblies?.prefix}-{nick_name}-{gene.ortho_group_row_number || '0'}
                  </Typography>
                </Box>
              )
            })}
        </Box>
      </Box>
      <AlertDialog
        openDialouge={openDialouge}
        sample_name={
          ortho_group?.[0]
            ? `${ortho_group[0]?.assemblies?.prefix || 'SamplePrefix'}-${
                nick_name || 'SampleNick'
              }-${ortho_group[0]?.ortho_group_row_number || '0'}`
            : 'SamplePrefix-SampleNick-0'
        }
        handleSubmitForm={handleFormSubmit}
      />

      <Box>
        <Button
          sx={{
            '&:hover': {
              backgroundColor: '#c3dbe3',
            },
          }}
          onClick={checkBeforeSubmit}
          disabled={loading}
        >
          {loading ? 'Submitting...' : 'Submit'}
        </Button>
        {loading && <CircularProgress size={24} sx={{ marginLeft: 2 }} />}
      </Box>
    </Box>
  )
}

function LookupModal({
  modal_state,
  closeModal,
}: {
  modal_state: boolean
  closeModal: () => void
}) {
  const style = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: '90%',
    height: '95%',
    bgcolor: 'background.paper',
    border: '2px solid #000',
    borderRadius: '16px',
    boxShadow: 24,
    p: 4,
  }

  const [loading, setLoading] = useState(false)
  const [lookup_option, setOptions] = useState<LookupOptions[]>([])
  const [inputValue, setInputValue] = useState('')
  const [ortho_genes, setOrthoGenes] = useState<OrthoGroupGeneList[]>([])
  const supabase = createClientComponentClient<Database>()

  const columns = [
    { field: 'gene_id', headerName: 'Gene ID', width: 200 },
    { field: 'standard_name', headerName: 'Standard Name', width: 180 },
    { field: 'symbol', headerName: 'Symbol', width: 120 },
    { field: 'ortho_group', headerName: 'Orthogroup', width: 150 },
    {
      field: 'assemblies',
      headerName: 'Assemblies',
      width: 200,
      valueGetter: (params: any) => {
        const assembly = params.row.assemblies
        return assembly?.prefix ?? '—'
      },
    },
  ]

  const fetchData_Lookup = async (query: string): Promise<LookupOptions[] | null> => {
    const { data: geneData, error: geneIdsError } = await supabase
      .from('genes')
      .select('gene_id,ortho_group,standard_name')
      .or(`standard_name.ilike.%${query}%,gene_id.ilike.${query}%`)
      .limit(10)
    if (geneIdsError) {
      console.error('Error fetching gene data:', geneIdsError)
    }
    return geneData as LookupOptions[] | null
  }

  const handleInputChange_Lookup = async (newInputValue: string): Promise<void> => {
    if (!newInputValue) {
      setOptions([])
      return
    }
    setLoading(true)
    try {
      const data = await fetchData_Lookup(newInputValue)
      setOptions(data || [])
    } catch (error) {
      console.error('Error fetching data:', error)
      setOptions([])
    } finally {
      setLoading(false)
    }
  }

  const handleSelectLookup = async (selected: LookupOptions | null) => {
    if (!selected || !selected.ortho_group) return

    let ortho_group = selected.ortho_group
    let batchSize = 1000
    let allResults: any[] = []
    let start = 0
    let done = false

    while (!done) {
      const { data, error } = await supabase
        .from('genes')
        .select('gene_id, ortho_group, standard_name, symbol, assemblies(id, prefix)')
        .eq('ortho_group', ortho_group)
        .range(start, start + batchSize - 1)

      if (error) throw error
      if (data.length < batchSize) {
        done = true
      }
      allResults = [...allResults, ...data]
      start += batchSize
    }
    setOrthoGenes(allResults)
  }

  useEffect(() => {
    return () => {
      debouncedInputChangeLookup.cancel()
    }
  }, [])

  const debouncedInputChangeLookup = _.debounce((_event: React.SyntheticEvent, value: string) => {
    handleInputChange_Lookup(value)
  }, 500)

  const handleModalClose = async () => {
    setOrthoGenes([])
    closeModal()
  }

  const handleDownloadFile = async () => {
    if (ortho_genes.length === 0) return

    const header = ['gene_id', 'standard_name', 'symbol', 'ortho_group', 'assembly_prefix']
    const csvRows = ortho_genes.map((gene) => {
      const prefix = gene.assemblies?.prefix ?? ''
      return [gene.gene_id, gene.standard_name, gene.symbol, gene.ortho_group, prefix].join(',')
    })

    const csvContent = [header.join(','), ...csvRows].join('\n')
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', 'orthogroup_genes.csv')
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  return (
    <>
      <Modal
        open={modal_state}
        onClose={handleModalClose}
        aria-labelledby="modal-modal-title"
        aria-describedby="modal-modal-description"
      >
        <Box sx={style}>
          <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
            <CloseIcon onClick={handleModalClose} sx={{ cursor: 'pointer' }} />
          </Box>
          <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
            <Typography id="modal-modal-title" variant="h6" component="h2">
              Lookup Gene Nicknames
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
            <Typography
              variant="subtitle1"
              sx={{ color: 'text.secondary', mt: 1, textAlign: 'center' }}
            >
              Search across all species for genes within the same orthogroup.
            </Typography>
          </Box>
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
              width: '100%',
              justifyItems: 'center',
            }}
          >
            <Autocomplete
              sx={{ width: '60%', margin: 'auto', marginTop: '40px' }}
              loading={loading}
              options={lookup_option}
              inputValue={inputValue}
              noOptionsText="No matching gene ID"
              getOptionLabel={(option) =>
                typeof option === 'string' ? option : `${option.gene_id} (${option.standard_name})`
              }
              onInputChange={(event, value, reason) => {
                setInputValue(value)
                debouncedInputChangeLookup(event, value)
              }}
              isOptionEqualToValue={(option, value) => option.gene_id === value.gene_id}
              onChange={(event, value) => {
                handleSelectLookup(value)
              }}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Search by gene nickname (standard name) or gene ID..."
                />
              )}
            />
          </Box>

          <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
            <Button
              variant="contained"
              sx={{
                backgroundColor: '#555 !important',
                color: 'white',
                '&:hover': {
                  backgroundColor: '#333',
                },
              }}
              onClick={handleDownloadFile}
              endIcon={<FileDownloadIcon />}
            >
              DOWNLOAD RESULT
            </Button>
          </Box>

          <Box sx={{ height: 450, mt: 6 }}>
            {ortho_genes.length > 0 && (
              <DataGrid
                rows={ortho_genes.map((ortho_genes, idx) => ({ id: idx, ...ortho_genes }))}
                columns={columns}
                initialState={{
                  pagination: {
                    paginationModel: { pageSize: 10 },
                  },
                }}
              />
            )}
          </Box>
        </Box>
      </Modal>
    </>
  )
}

function ModalBox({
  modal_box,
  openModal,
  closeModal,
}: {
  modal_box: boolean
  openModal: () => void
  closeModal: () => void
}) {
  const [option, setOptions] = useState<{ gene_id: string; ortho_group: string | null }[]>([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [form, setForm] = useState(false)
  const [ortho_group, setOrthoGroup] = useState<OrthoGroupData[] | null>(null)
  const [inputValue, setInputValue] = useState('')
  const [selected_gene, setSelectedGene] = useState('')
  const [nick_name, setNickName] = useState('')
  const supabase = createClientComponentClient<Database>()

  const style = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: '90%',
    height: 800,
    bgcolor: 'background.paper',
    border: '2px solid #000',
    borderRadius: '16px',
    boxShadow: 24,
    p: 4,
  }

  const handleClose = () => {
    closeModal()
    setForm(false)
    setMessage(null)
    setOrthoGroup(null)
  }

  const fetchSingleGeneInfo = async (gene_id: string) => {
    if (!gene_id) {
      setMessage('Error: Invalid gene ID')
      return
    }
    const { data, error } = await supabase
      .from('genes')
      .select(
        `ortho_group,
          gene_id,
          ortho_group_row_number,
          short_id,
          assemblies(
            id,
            prefix
          ) `
      )
      .eq('gene_id', gene_id)
      .returns<OrthoGroupData[]>()

    if (error) {
      console.error('Error fetching genes table:', error)
      return []
    }
    return data
  }

  const fetchOrthoGenes = async (ortho_group: string) => {
    if (!ortho_group) return null

    let allData: OrthoGroupData[] = []
    let hasMoreData = true
    let offset = 0
    const limit = 1000

    while (hasMoreData) {
      const { data, error } = await supabase
        .from('genes')
        .select(
          `
          ortho_group,
          gene_id,
          ortho_group_row_number,
          short_id,
          assemblies(
            id,
            prefix
          )
        `
        )
        .eq('ortho_group', ortho_group)
        .range(offset, offset + limit - 1)
        .returns<OrthoGroupData[]>()

      if (error) {
        console.error('Error fetching ortho genes:', error)
        return []
      }

      if (data && data.length > 0) {
        allData = [
          ...allData,
          ...data.map((item) => ({
            ...item,
            assemblies: item.assemblies || { id: 0, prefix: null },
          })),
        ]
        offset += limit
      } else {
        hasMoreData = false
      }
    }

    return allData
  }

  const fetchData = async (query: string): Promise<GeneData[] | null> => {
    const { data: geneData, error: geneIdsError } = await supabase
      .from('genes')
      .select('gene_id, ortho_group', { count: 'exact' })
      .ilike('gene_id', `${query}%`)
      .limit(10)

    if (geneIdsError) {
      console.error('Error fetching gene data:', geneIdsError)
    }
    return geneData as GeneData[] | null
  }

  const handleChange = async (value: string | null) => {
    if (!value) return

    const { data: gene_std, error: gene_stderr } = await supabase
      .from('genes')
      .select('standard_name, gene_id, ortho_group')
      .eq('gene_id', value)
      .single<{
        standard_name: string | null
        gene_id: string
        ortho_group: string | null
      }>()

    const std_name = gene_std ? gene_std.standard_name : null
    const ortho_group: string | null = gene_std ? gene_std.ortho_group : null

    if (!std_name && ortho_group) {
      let orthogroups = await fetchOrthoGenes(ortho_group)
      setOrthoGroup(orthogroups || [])
      setForm(true)
    } else if (!std_name) {
      let geneInfo = await fetchSingleGeneInfo(value)
      setOrthoGroup(geneInfo || [])
      setForm(true)
    } else {
      setMessage('A standard name is already assigned to this Gene ID.')
    }
  }

  const handleInputChange = async (newInputValue: string): Promise<void> => {
    setInputValue(newInputValue)
    if (!newInputValue) {
      setOptions([])
      return
    }
    setLoading(true)
    fetchData(newInputValue)
      .then((data) => {
        setOptions(data || [])
      })
      .catch((error) => {
        console.error('Error fetching data:', error)
        setOptions([])
        setLoading(false)
      })
      .finally(() => {
        setLoading(false)
      })
  }

  const debouncedInputChange = _.debounce((_event: React.SyntheticEvent, value: string) => {
    handleInputChange(value)
  }, 500)

  return (
    <>
      <div>
        <Modal
          open={modal_box}
          onClose={handleClose}
          aria-labelledby="modal-modal-title"
          aria-describedby="modal-modal-description"
        >
          <Box sx={style}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                width: '100%',
              }}
            >
              <Typography
                id="modal-modal-title"
                variant="h6"
                component="h2"
                style={{ flex: 1, textAlign: 'center' }}
              >
                SET GENE STANDARD NAME (NICKNAME)
              </Typography>
              <CloseIcon style={{ marginLeft: 'auto', cursor: 'pointer' }} onClick={handleClose} />
            </div>
            {message && (
              <Alert
                severity="error"
                action={
                  <CloseIcon onClick={() => setMessage(null)} style={{ cursor: 'pointer' }} />
                }
              >
                {message}
              </Alert>
            )}

            <Autocomplete
              sx={{ width: '90%', margin: '20px', marginTop: '40px' }}
              disablePortal
              loading={loading}
              options={option}
              getOptionLabel={(option) => option.gene_id}
              onInputChange={(event, value, reason) => debouncedInputChange(event, value)}
              isOptionEqualToValue={(option, value) => option.gene_id === value.gene_id}
              onChange={(event, value) => handleChange(value?.gene_id || null)}
              renderInput={(params) => <TextField {...params} label="Enter Gene Id" />}
            />

            <TextField
              sx={{ width: '90%', margin: '20px', marginTop: '40px' }}
              id="nick-name-text"
              label="Enter Standard Name (Nick Name)"
              variant="outlined"
              onChange={(e) => setNickName(e.target.value)}
            />
            {form && (
              <FormContent
                ortho_group={ortho_group}
                nick_name={nick_name}
                closeModalBox={handleClose}
                selected_gene={selected_gene}
              />
            )}
          </Box>
        </Modal>
      </div>
    </>
  )
}

export default function Genes() {
  // const user = await getUser();

  // const mixpanel = process.env.MIXPANEL_TOKEN
  //   ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
  //   : null;

  // mixpanel?.track("Page view", {
  //   distinct_id: user?.email,
  //   url: "/app/genes",
  // });

  const [geneCandidates, setGeneCandidates] = useState<GeneRow[] | null>(null)
  const [selectedStatus, setSelectedStatus] = useState<Status | null>(null)
  const [modal_box, setModalBox] = useState(false)
  const [candidate_modal, setCandidateModal] = useState(false)
  const [lookup_modal, setLookupModal] = useState(false)
  const [openCandidate, setOpenCandidate] = useState<string | null>(null)
  const [currentGeneCandidate, setCurrentGeneCandidate] = useState<GeneRow | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const handleOpen = () => setModalBox(true)
  const handleClose = () => setModalBox(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [newCandidateAdded, setNewCandidateAdded] = useState(false)
  const [currentStatus, setCurrentStatus] = useState<boolean>(false)
  const [selectedGene, setSelectedGene] = useState<string | null>(null)

  useEffect(() => {
    getGeneCandidates().then((data) => setGeneCandidates(data))
  }, [])

  useEffect(() => {
    if (newCandidateAdded) {
      console.log('New candidate added, fetching data again')
      getGeneCandidates().then((data) => setGeneCandidates(data))
      // getGeneCandidates().then(
      //   (data) => setGeneCandidates(data)
      //   console.log("Fetched updated candidates:", data);
      // );

      setNewCandidateAdded(false)
    }
  }, [newCandidateAdded])

  const handleModalClick = () => {
    setModalBox(true)
  }

  const handleSearchChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(event.target.value)
  }

  const handleGeneCandidateModalOpen = () => {
    setCandidateModal(true)
  }

  const handleGeneCandidateModalClose = () => {
    setCandidateModal(false)
  }

  const handleGeneLookupModalOpen = () => {
    setLookupModal(true)
  }

  const handleGeneLookupModalClose = () => {
    setLookupModal(false)
  }

  const handleCurrentStatus = (value: boolean) => {
    setCurrentStatus(value)
  }

  const handleExperimentLogsOpen = (geneId: string) => {
    console.log('Open Modal with ' + geneId)
    setOpenCandidate(geneId)
    const selected = geneCandidates?.find((c) => c.gene === geneId) || null
    // console.log("Selected Gene Candidate", selected);
    setCurrentGeneCandidate(selected)
  }

  const handleExperimentLogsOpenFromSidebar = () => {
    setOpenCandidate('generic')
    setCurrentGeneCandidate(null)
  }

  const handleCandidateSelectionChange = (geneId: string) => {
    const selected = geneCandidates?.find((c) => c.gene === geneId) || null
    setCurrentGeneCandidate(selected)
  }

  const handleExperimentLogsClose = () => {
    setOpenCandidate(null)
  }

  return (
    <>
      <Drawer
        anchor="right"
        variant="permanent"
        open={drawerOpen}
        PaperProps={{
          sx: {
            width: drawerOpen ? 200 : 60,
            transition: 'width 0.3s',
            overflowX: 'hidden',
            top: '64px',
            height: 'calc(100% - 64px)',

            borderLeft: '1px solid rgba(25, 118, 210, 0.3)',
            borderTop: '1px solid rgba(25, 118, 210, 0.3)',
            borderRadius: '16px 0 0 16px',
            boxShadow: '-4px 0 10px rgba(25, 118, 210, 0.3)',
            backgroundColor: '#f5faff',
          },
        }}
      >
        <List sx={{ pt: 1 }}>
          <ListItem sx={{ justifyContent: 'center' }}>
            <IconButton onClick={() => setDrawerOpen(!drawerOpen)}>
              {drawerOpen ? <ChevronRightIcon /> : <ChevronLeftIcon />}
            </IconButton>
          </ListItem>

          {/* Button 1 */}
          <Tooltip
            title="Add gene nickname (standard names) across gene families"
            placement="left"
            arrow
          >
            <ListItem
              component="button"
              sx={{
                justifyContent: drawerOpen ? 'initial' : 'center',
                px: 2,
              }}
              onClick={handleModalClick}
            >
              <ListItemIcon
                sx={{
                  minWidth: 0,
                  mr: drawerOpen ? 2 : 'auto',
                  justifyContent: 'center',
                }}
                onClick={handleModalClick}
              >
                <DashboardIcon />
              </ListItemIcon>
              {drawerOpen && (
                <ListItemText
                  primary="ADD GENE NICKNAME"
                  primaryTypographyProps={{
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    color: 'text.primary',
                  }}
                  onClick={handleModalClick}
                />
              )}
            </ListItem>
          </Tooltip>

          {/* Button 2 */}
          <Tooltip title="Mark a gene as a candidate for study" placement="left" arrow>
            <ListItem
              component="button"
              sx={{ justifyContent: drawerOpen ? 'initial' : 'center', px: 2 }}
            >
              <ListItemIcon
                sx={{ minWidth: 0, mr: drawerOpen ? 2 : 'auto', justifyContent: 'center' }}
                onClick={handleGeneCandidateModalOpen}
              >
                <LabelIcon />
              </ListItemIcon>
              {drawerOpen && (
                <ListItemText
                  primary="ADD NEW GENE CANDIDATE"
                  primaryTypographyProps={{
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    color: 'text.primary',
                  }}
                  onClick={handleGeneCandidateModalOpen}
                />
              )}
            </ListItem>
          </Tooltip>

          {/* Button 3 */}
          <Tooltip title="Look up all genes using standard names" placement="left" arrow>
            <ListItem
              component="button"
              sx={{ justifyContent: drawerOpen ? 'initial' : 'center', px: 2 }}
            >
              <ListItemIcon
                sx={{ minWidth: 0, mr: drawerOpen ? 2 : 'auto', justifyContent: 'center' }}
                onClick={handleGeneLookupModalOpen}
              >
                <SearchIcon />
              </ListItemIcon>
              {drawerOpen && (
                <ListItemText
                  primary="LOOKUP NICKNAME"
                  primaryTypographyProps={{
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    color: 'text.primary',
                  }}
                  onClick={handleGeneLookupModalOpen}
                />
              )}
            </ListItem>
          </Tooltip>

          {/* Button 4 */}
          <Tooltip title="Track updates for any gene candidate" placement="left" arrow>
            <ListItem
              component="button"
              sx={{ justifyContent: drawerOpen ? 'initial' : 'center', px: 2 }}
            >
              <ListItemIcon
                sx={{ minWidth: 0, mr: drawerOpen ? 2 : 'auto', justifyContent: 'center' }}
                onClick={() => handleExperimentLogsOpen(geneCandidates?.[0].gene || 'null')}
              >
                <LibraryBooksIcon />
              </ListItemIcon>
              {drawerOpen && (
                <ListItemText
                  primary="ADD EXPERIMENT PROGRESS LOGS"
                  primaryTypographyProps={{
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    color: 'text.primary',
                  }}
                  onClick={() => handleExperimentLogsOpen(geneCandidates?.[0].gene || 'null')}
                />
              )}
            </ListItem>
          </Tooltip>
        </List>
      </Drawer>

      <div>
        <div className="italic text-xl mb-8 select-none">Genes</div>
        {/* <div className="mb-6 mt-6 select-none">Gene candidates</div> */}
        {/* Status counts */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'space-between',
            marginBottom: '20px',
            width: '100%',
            gap: '20px',
          }}
        >
          <TextField
            label="Search Genes"
            variant="outlined"
            value={searchQuery}
            onChange={handleSearchChange}
            sx={{ width: '60%' }}
          />
        </div>
        <ModalBox modal_box={modal_box} openModal={handleOpen} closeModal={handleClose} />
        <AddGeneCandidateModal
          modal_state={candidate_modal}
          closeModal={handleGeneCandidateModalClose}
          setNewCandidateAdded={setNewCandidateAdded}
        />
        <LookupModal modal_state={lookup_modal} closeModal={handleGeneLookupModalClose} />
        <CurrentStatus
          open={currentStatus}
          setOpen={setCurrentStatus}
          selectedGene={selectedGene}
        />
        <div className="mb-6">
          {geneCandidates ? (
            <div className="flex flex-row">
              {Object.entries(getStatusCounts(geneCandidates)).map(([status, count]) => (
                <div
                  key={status}
                  className={
                    'flex flex-col items-center mr-4 text-sm p-2 rounded-md border cursor-pointer hover:border-gray-300 ' +
                    (selectedStatus === status ? 'bg-white border-gray-300' : 'border-stone-100')
                  }
                  onClick={() => {
                    if (selectedStatus === status) setSelectedStatus(null)
                    else setSelectedStatus(status as Status)
                  }}
                >
                  <div className="text-2xl mb-1">{count}</div>
                  <div className="text-xs">
                    <Status
                      status={status as Status}
                      handleCurrentStatus={handleCurrentStatus}
                      geneCandidate={'UNKNOWN'}
                      setSelectedGene={setSelectedGene}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
        {/* <div className="mb-4 h-[600px] overflow-scroll border-2 w-[800px] p-4 rounded-md"> */}
        <table className="rounded-md">
          <thead>
            <tr>
              <th className="text-xs text-left px-2 pb-4 align-bottom">Gene ID</th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">Symbol</th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">Standard Name (Nickname)</th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">
                Current <br /> Status
              </th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">Category</th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">
                Discovery
                <br />
                Scientists
              </th>
              {/* <th className="text-xs text-left px-2 pb-4 align-bottom">
                Evidence
              </th> */}
              <th className="text-xs text-left px-2 pb-4 align-bottom">
                Evidence &
                <br /> Progress Logs
              </th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">Published</th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">
                Disclosed
                <br /> to OTD
              </th>
              <th className="text-xs text-left px-2 pb-4 align-bottom">
                Evaluated for
                <br /> translation
              </th>
              {/* <th className="text-xs text-left px-2 pb-4 align-bottom"></th> */}
            </tr>
          </thead>
          <tbody>
            {geneCandidates
              ?.filter((row) => {
                const searchText = searchQuery.toLowerCase()
                return (
                  row.gene.toLowerCase().includes(searchText) ||
                  row.genes?.symbol?.toLowerCase().includes(searchText) ||
                  row.genes?.standard_name?.toLowerCase().includes(searchText)
                )
              })
              ?.filter((row) => selectedStatus === null || selectedStatus === row.status)
              .map((row) => (
                <tr key={row.gene} className="odd:bg-stone-200">
                  <td className="text-left p-2">
                    {row.genes?.assemblies?.hpi_reference_id && row.genes?.short_id ? (
                      <a
                        href={`/app/jbrowse?reference=${row.genes.assemblies.hpi_reference_id}&gene=${row.genes.short_id}`}
                        className="text-blue-500 underline"
                      >
                        {row.genes?.short_id || row.gene}
                      </a>
                    ) : (
                      row.genes?.short_id || row.gene
                    )}
                  </td>
                  <td className="text-left p-2">{row.genes?.symbol || null}</td>
                  <td className="text-left p-2">{row.genes?.standard_name || null}</td>
                  <td className="text-left p-2">
                    <Status
                      status={row.status as Status}
                      handleCurrentStatus={handleCurrentStatus}
                      geneCandidate={row.gene}
                      setSelectedGene={setSelectedGene}
                    />
                  </td>
                  <td className="text-left p-2">{row.category}</td>
                  <td className="text-left p-2">
                    {row.people.map((person, index: number) => {
                      return (
                        <div key={person.id} className="inline">
                          <Person person={person} />
                          {index === row.people.length - 1 ? '' : ', '}
                        </div>
                      )
                    })}
                  </td>
                  {/* <td className="text-left p-2">
                    <Evidence candidate={row} />
                  </td> */}
                  <td className="text-left p-2">
                    <Progress
                      candidate={row}
                      candidates_list={geneCandidates}
                      currentGeneCandidate={currentGeneCandidate}
                      isOpen={openCandidate === row.gene || openCandidate === 'generic'}
                      handleOpen={() => handleExperimentLogsOpen(row.gene)}
                      handleExperimentLogsClose={handleExperimentLogsClose}
                      handleCandidateChange={handleCandidateSelectionChange}
                    />
                  </td>
                  <td className="text-left p-2">
                    <Published candidate={row} />
                  </td>
                  <td className="text-left p-2">
                    <Disclosed candidate={row} />
                  </td>
                  <td className="text-left p-2">
                    <Translation candidate={row} />
                  </td>
                  {/* <td className="text-left p-2">Edit</td> */}
                </tr>
              ))}
          </tbody>
        </table>
        {/* </div> */}
      </div>
    </>
  )
}

async function getGeneCandidates() {
  const supabase = createClientComponentClient<Database>()

  const { data } = await supabase
    .from('gene_candidates')
    .select(
      `
      *,
      people(*),
      genes(
        short_id,
        symbol,
        standard_name,
        assemblies(
          hpi_reference_id
        )
      )
    `
    )
    .order('created_at', { ascending: false })
    .order('category', { ascending: true })
    .order('gene', { ascending: true })

  return data as GeneRow[] | null
}
function Person({ person }: { person: PersonRow }) {
  const firstName = person.name?.split(' ')[0]
  const email = person.email
  return (
    <span title={person.name || ''}>
      {email ? (
        <a className="text-lime-700 hover:underline" href={'mailto:' + email}>
          {firstName}
        </a>
      ) : (
        firstName
      )}
    </span>
  )
}

function Evidence({ candidate }: { candidate: GeneRow }) {
  return (
    <div className="container">
      <div className="hover-target cursor-default bg-white rounded-md border border-gray-300 text-sm p-1 hover:border-gray-600">
        Evidence
      </div>
      <div className="reveal-on-hover text-sm bg-white border border-gray-600 p-2 rounded-md w-96 ml-1 shadow z-10">
        {candidate.evidence_description || <span className="italic">None</span>}
      </div>
    </div>
  )
}

function Published({ candidate }: { candidate: GeneRow }) {
  return (
    <div className="">
      {candidate.publication_status ? (
        <div className="cursor-default rounded-md inline border border-gray-300 text-sm p-1 bg-gray-200 text-gray-800">
          Published
        </div>
      ) : null}
    </div>
  )
}

function Disclosed({ candidate }: { candidate: GeneRow }) {
  return (
    <div className="">
      {candidate.disclosed_to_otd ? (
        <div className="cursor-default rounded-md inline border border-gray-300 text-sm p-1 bg-gray-200 text-gray-800">
          Disclosed
        </div>
      ) : null}
    </div>
  )
}

function Translation({ candidate }: { candidate: GeneRow }) {
  return (
    <div className="">
      {candidate.translation_approval_date ? (
        <div className="cursor-default bg-white rounded-md border border-gray-300 text-sm p-1">
          {candidate.translation_approval_date}
        </div>
      ) : null}
    </div>
  )
}

const statusColors = {
  stopped: { textColor: 'text-red-700', bgColor: 'bg-red-100' },
  suspected: {
    textColor: 'text-orange-700',
    bgColor: 'bg-orange-100',
  },
  'under-investigation': {
    textColor: 'text-yellow-700',
    bgColor: 'bg-yellow-100',
  },
  'in-translation': { textColor: 'text-lime-700', bgColor: 'bg-lime-100' },
  'translation-confirmed': {
    textColor: 'text-blue-700',
    bgColor: 'bg-blue-100',
  },
}

type Status =
  | 'stopped'
  | 'suspected'
  | 'under-investigation'
  | 'in-translation'
  | 'translation-confirmed'

function Status({
  status,
  handleCurrentStatus,
  geneCandidate,
  setSelectedGene,
}: {
  status: Status
  handleCurrentStatus: (value: boolean) => void
  geneCandidate: string
  setSelectedGene: (value: string) => void
}) {
  const colors = statusColors[status]

  return (
    <div className="">
      <div
        onClick={() => {
          handleCurrentStatus(true)
          setSelectedGene(geneCandidate)
        }}
        className={`rounded-md inline border border-gray-300 text-sm px-3 py-1
            ${colors.textColor} ${colors.bgColor}
            cursor-pointer transition duration-200
            hover:brightness-95 hover:shadow-sm`}
      >
        {status}
      </div>
    </div>
  )
}

function getStatusCounts(geneCandidates: GeneRow[]) {
  const statusCounts = {
    suspected: 0,
    'under-investigation': 0,
    stopped: 0,
    'in-translation': 0,
    'translation-confirmed': 0,
  }
  geneCandidates.forEach((row) => {
    statusCounts[row.status as Status]++
  })
  return statusCounts
}
