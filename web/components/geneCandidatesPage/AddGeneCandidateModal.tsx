import { useEffect, useState, Fragment, useRef } from 'react'
import { Dispatch, SetStateAction } from 'react'
import Modal from '@mui/material/Modal'
import Box from '@mui/material/Box'
import CloseIcon from '@mui/icons-material/Close'
import Autocomplete from '@mui/material/Autocomplete'
import Typography from '@mui/material/Typography'
import TextField from '@mui/material/TextField'
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'
import type { Database } from '@/lib/database.types'
import * as TypeDefs from '../../types/genecandidates'
import AddNewCategory from './AddNewCategory'
import AddNewScientist from './AddNewScientist'
import { Alert, AlertTitle } from '@mui/material'

import { Button, Checkbox, FormControlLabel } from '@mui/material'
import { gray } from 'd3'
import type { SupabaseClient } from '@supabase/supabase-js'

export default function AddGeneCandidateModal({
  modal_state,
  closeModal,
  setNewCandidateAdded,
}: {
  modal_state: boolean
  closeModal: () => void
  setNewCandidateAdded: Dispatch<SetStateAction<boolean>>
}) {
  const supabase = createClientComponentClient<Database>() as unknown as SupabaseClient<Database>
  const [description, setDescription] = useState('')
  const [disclosedToOtd, setDisclosedToOtd] = useState(false)
  const [publicationStatus, setPublicationStatus] = useState(false)
  const [evaluatedTranslation, setEvaluatedTranslationStatus] = useState(false)
  const [evaluationDate, setEvaluationDate] = useState('')
  const [category, setCategory] = useState('')
  const [speciesOptions, setSpeciesOptions] = useState<TypeDefs.SpeciesList[]>([])
  const [selectedSpecies, setSelectedSpecies] = useState<string | number | null>(null)
  const [geneList, setGeneList] = useState<string[]>([])
  const [geneCandidate, setGeneCandidate] = useState<string | null>(null)
  const [categoryOptions, setCategoryOptions] = useState<TypeDefs.Category[]>([])
  const [peopleList, setPeopleList] = useState<TypeDefs.People[]>([])
  const [selectedPeople, setSelectedPeople] = useState<TypeDefs.People | null>(null)
  const [selectedStatus, setSelectedStatus] = useState<TypeDefs.Status | null>('suspected')
  const [message, setMessage] = useState<string | null>(null)
  const [alertMessage, setAlertMessage] = useState<string | null>(null)
  const isFormValid = selectedSpecies && geneCandidate && category && selectedPeople

  const style = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: '90%',
    height: 860,
    bgcolor: 'background.paper',
    border: '2px solid #000',
    borderRadius: '16px',
    boxShadow: 24,
    p: 4,
  }

  const statusColors: Record<TypeDefs.Status, { textColor: string; bgColor: string }> = {
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

  const fetchGeneOptionsSearch = async () => {
    if (selectedSpecies !== null) {
      const { data, error } = await supabase
        .from('genes')
        .select('gene_id')
        .eq('gene_id', `%${geneCandidate}%`)
        .eq('reference_id', Number(selectedSpecies))
        .limit(10)
      if (error) {
        console.error('Error fetching species:', error)
        return
      }
      const geneList = data.map((gene: { gene_id: string }) => gene.gene_id)
      setGeneList(geneList)
    }
    // if (error) {
    //   console.log("Error fetching species:", error);
    //   return;
    // }
    // const geneList = data.map((gene: { gene_id: string }) => gene.gene_id);
    // setGeneList(geneList);
  }

  useEffect(() => {
    if (!message) return
    const timer = setTimeout(() => {
      setMessage(null)
    }, 6000)
    closeModal()
    return () => clearTimeout(timer)
  }, [message])

  useEffect(() => {
    const fetchSpeciesOptions = async () => {
      const { data, error } = await supabase
        .from('assemblies')
        .select('id, species_id,species(common_name), origin, accession_name, hpi_assembly')

      if (error) {
        console.error('Error fetching species:', error)
        return
      }
      setSpeciesOptions(data)
    }
    fetchSpeciesOptions()
  }, [])

  useEffect(() => {
    if (!geneCandidate) return
    fetchGeneOptionsSearch
  }, [geneCandidate])

  useEffect(() => {
    if (evaluatedTranslation && !evaluationDate) {
      const today = new Date().toISOString().split('T')[0]
      setEvaluationDate(today)
    }
  }, [evaluatedTranslation])

  useEffect(() => {
    if (!selectedSpecies) return

    const fetchGeneOptions = async () => {
      const { data, error } = await supabase
        .from('genes')
        .select('gene_id')
        .eq('reference_id', Number(selectedSpecies))
        .limit(10)

      if (error) {
        console.error('Error fetching species:', error)
        return
      }
      const geneList = data.map((gene: { gene_id: string }) => gene.gene_id)
      setGeneList(geneList)
    }

    const fetchCategoryOptions = async () => {
      const { data, error } = await supabase.rpc('get_unique_categories')
      console.log(data)

      if (error) {
        console.error('Error fetching Categories:', error)
        setCategoryOptions([])
        return
      }
      setCategoryOptions(data)
    }

    const fetchDiscoveryScientistOptions = async () => {
      const { data, error } = await supabase.from('people').select('*')

      if (error) {
        console.error('Error fetching Categories:', error)
        setPeopleList([])
        return
      }
      setPeopleList(data)
    }

    fetchGeneOptions()
    fetchCategoryOptions()
    fetchDiscoveryScientistOptions()
  }, [selectedSpecies])

  const fetchUser = async (): Promise<string> => {
    try {
      const {
        data: { user },
        error,
      } = await supabase.auth.getUser()
      if (error || !user) {
        console.error('Failed to get user:', error)
        return 'Unknown'
      }
      return user.email || user.id || 'Unknown'
    } catch (err) {
      console.error('Unexpected error while fetching user:', err)
      return 'Unknown'
    }
  }

  const postSubmission = async () => {
    let scientistId = selectedPeople?.id || null

    if (selectedPeople?.id === 0) {
      const { data, error } = await supabase
        .from('people')
        .insert({
          name: selectedPeople.name,
          email: selectedPeople.email,
        })
        .select('id')
        .single()

      if (error || !data?.id) {
        console.error('Error adding new scientist:', error)
        return
      }
      scientistId = data.id
    }

    let user_email = await fetchUser()

    const { data: candidateData, error: candidateError } = await supabase
      .from('gene_candidates')
      .insert({
        gene: geneCandidate!,
        evidence_description: description || null,
        category,
        disclosed_to_otd: disclosedToOtd,
        publication_status: publicationStatus,
        translation_approval_date: evaluatedTranslation ? evaluationDate || null : null,
        status: selectedStatus || 'suspected',
        status_logs: { userid: user_email, status: 'suspected', date: new Date().toISOString() },
      })

    if (candidateError) {
      console.error('Error inserting gene candidate:', candidateError)
      setAlertMessage(candidateError.message)
      return false
    }

    if (scientistId !== null && geneCandidate !== null) {
      const { error: linkError } = await supabase.from('gene_candidate_scientists').insert({
        gene_candidate_id: geneCandidate,
        scientist_id: scientistId,
      })
      if (linkError) {
        console.error('Error linking scientist to candidate:', linkError)
        return false
      }
    } else {
      console.error('Scientist ID is null, cannot link scientist to candidate.')
      return false
    }
    return true
    // console.log("Successfully linked candidate with scientist:", {
    //   gene: geneCandidate,
    //   scientistId,
    // });
  }

  const handleSubmit = async () => {
    const res = await postSubmission()

    if (res === true) {
      // Reset the form fields after successful submission
      setNewCandidateAdded((prevState) => !prevState)
      setMessage('Successfully added new gene candidate!')
    }
    // Reset all form fields on failure
    setSelectedSpecies(null)
    setGeneCandidate(null)
    setDescription('')
    setSelectedPeople(null)
    setDisclosedToOtd(false)
    setPublicationStatus(false)
    setEvaluationDate('')
    setCategory('')
    setSelectedStatus('suspected')
    setGeneList([])
  }

  const onCloseModal = () => {
    setAlertMessage(null)
    setSelectedSpecies(null)
    setGeneCandidate(null)
    setDescription('')
    setSelectedPeople(null)
    setDisclosedToOtd(false)
    setPublicationStatus(false)
    setEvaluationDate('')
    setCategory('')
    setSelectedStatus('suspected')
    setGeneList([])
    closeModal()
  }

  return (
    <>
      <Modal
        open={modal_state}
        onClose={onCloseModal}
        aria-labelledby="modal-modal-title"
        aria-describedby="modal-modal-description"
      >
        <Box sx={style}>
          <Box sx={{ maxHeight: 820, overflowY: 'auto', pr: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
              <CloseIcon onClick={onCloseModal} sx={{ cursor: 'pointer' }} />
            </Box>

            {alertMessage && (
              <Alert severity="error" onClose={() => setAlertMessage(null)} sx={{ mb: 2 }}>
                <AlertTitle>Notice</AlertTitle>
                {alertMessage && <strong>Gene candidate already exists!</strong>}
              </Alert>
            )}

            <Box sx={{ display: 'flex', justifyContent: 'center', mb: 2 }}>
              <Typography id="modal-modal-title" variant="h6" component="h2">
                ADD A NEW GENE CANDIDATE
                {message && (
                  <Typography
                    variant="body2"
                    sx={{ color: 'green', fontSize: 16, fontWeight: 400, mt: 1 }}
                    className="text-center"
                    key={message}
                  >
                    {message}
                  </Typography>
                )}
              </Typography>
            </Box>
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                gap: 2,
                width: '100%',
              }}
            >
              <Autocomplete
                options={speciesOptions}
                getOptionLabel={(option) =>
                  `${option.species?.common_name} (${option.hpi_assembly} - ${option.accession_name})`
                }
                onChange={(e, value) => setSelectedSpecies(value?.id || null)}
                renderInput={(params) => <TextField {...params} label="Select Species" />}
              />

              <Autocomplete
                options={geneList}
                getOptionLabel={(option) => option}
                onChange={(e, value) => setGeneCandidate(value)}
                renderInput={(params) => <TextField {...params} label="Select Gene" />}
              />

              <TextField
                label="Evidence Description"
                placeholder="Enter evidence description"
                multiline
                rows={2}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />

              {/* Select Category */}

              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  width: '100%',
                  gap: 2,
                }}
              >
                <Autocomplete
                  sx={{
                    width: '90%',
                  }}
                  options={categoryOptions || []}
                  getOptionLabel={(option) => option?.category || ''}
                  onChange={(e, value) => setCategory(value?.category || '')}
                  renderInput={(params) => <TextField {...params} label="Select Category" />}
                />

                <AddNewCategory setCategory={setCategoryOptions} categories={categoryOptions} />
              </Box>

              {/* Select Scientist */}

              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  width: '100%',
                  gap: 2,
                }}
              >
                <Autocomplete
                  sx={{ width: '90%' }}
                  options={peopleList || []}
                  getOptionLabel={(option) => `${option.name} (${option.email})`}
                  onChange={(e, value) => setSelectedPeople(value || null)}
                  isOptionEqualToValue={(option, value) => option.id === value.id}
                  renderInput={(params) => (
                    <TextField {...params} label="Select Discovery Scientist" />
                  )}
                />

                <AddNewScientist setPeopleList={setPeopleList} peopleList={peopleList} />
              </Box>

              <FormControlLabel
                control={
                  <Checkbox
                    checked={disclosedToOtd}
                    onChange={(e) => setDisclosedToOtd(e.target.checked)}
                  />
                }
                label="Disclosed to OTD"
              />

              <FormControlLabel
                control={
                  <Checkbox
                    checked={publicationStatus}
                    onChange={(e) => setPublicationStatus(e.target.checked)}
                  />
                }
                label="Published"
              />

              <FormControlLabel
                control={
                  <Checkbox
                    checked={evaluatedTranslation}
                    onChange={(e) => setEvaluatedTranslationStatus(e.target.checked)}
                  />
                }
                label="Evaluated for Translation"
              />

              {evaluatedTranslation && (
                <TextField
                  label="Evaluation Date"
                  type="date"
                  InputLabelProps={{ shrink: true }}
                  value={evaluationDate}
                  onChange={(e) => setEvaluationDate(e.target.value)}
                  sx={{ mt: 2, width: 220 }}
                />
              )}

              <Box className="flex flex-wrap gap-2 mt-2">
                <Typography variant="subtitle1" sx={{ mt: 0, mb: 0, color: 'black' }}>
                  Select status:
                </Typography>

                {(Object.keys(statusColors) as TypeDefs.Status[]).map((status) => {
                  const colors = statusColors[status]
                  const isSelected = selectedStatus === status

                  return (
                    <Box
                      sx={{ mb: 1 }}
                      key={status}
                      className={
                        `cursor-pointer rounded-md border border-gray-300 text-sm px-2 py-1
                      ${colors.textColor} ${colors.bgColor} ` +
                        (isSelected ? ' ring-2 ring-offset-2 ring-blue-400' : '')
                      }
                      onClick={() => setSelectedStatus(status)}
                    >
                      {status}
                    </Box>
                  )
                })}
              </Box>

              <Button
                variant="contained"
                onClick={handleSubmit}
                disabled={!isFormValid}
                sx={{
                  mt: 3,
                  color: 'white',
                  backgroundColor: '#1976d2 !important',
                  '&:hover': {
                    backgroundColor: '#1565c0 !important',
                  },
                  '&.Mui-disabled': {
                    backgroundColor: 'e0e0e0# !important',
                    color: '#9e9e9e',
                  },
                }}
              >
                Submit
              </Button>

              {!isFormValid && (
                <Typography
                  variant="body2"
                  color="error"
                  sx={{ mt: 1, alignItems: 'center', display: 'flex', justifyContent: 'center' }}
                >
                  Please fill all required values to enable submission.
                </Typography>
              )}
            </Box>
          </Box>
        </Box>
      </Modal>
    </>
  )
}
