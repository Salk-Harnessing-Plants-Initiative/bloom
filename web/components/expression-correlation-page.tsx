import * as React from 'react'
import Tabs from '@mui/material/Tabs'
import Tab from '@mui/material/Tab'
import Box from '@mui/material/Box'
import ExpressionCorrelation from './expression-correlation-section'
import CorrelationAnalysis from './expression-correlation-analysis'

interface TabPanelProps {
  children?: React.ReactNode
  index: number
  value: number
}

function CustomTabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`correlation-tabpanel-${index}`}
      aria-labelledby={`correlation-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
    </div>
  )
}

function a11yProps(index: number) {
  return {
    id: `correlation-tab-${index}`,
    'aria-controls': `correlation-tabpanel-${index}`,
  }
}

export default function BasicTabs({ file_id }: { file_id: number }) {
  const [value, setValue] = React.useState(0)

  const handleChange = (event: React.SyntheticEvent, newValue: number) => {
    setValue(newValue)
  }

  return (
    <Box sx={{ width: '100%' }}>
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs
          value={value}
          onChange={handleChange}
          TabIndicatorProps={{ style: { display: 'none' } }}
          sx={{
            '& .MuiTab-root': {
              backgroundColor: '#f0f0f0',
              borderRadius: '8px',
              margin: '0 5px',
              textTransform: 'none',
              fontWeight: 'bold',
              padding: '10px 20px',
              '&.Mui-selected': {
                backgroundColor: '#2d63ed',
                color: 'white',
              },
            },
          }}
        >
          <Tab label="Co-Expression Network (Top 3,000 Genes)" {...a11yProps(0)} />
          <Tab label="Gene Correlation Analysis (Select & Compare)" {...a11yProps(1)} />
        </Tabs>
      </Box>
      <CustomTabPanel value={value} index={0}>
        <ExpressionCorrelation file_id={file_id} />
      </CustomTabPanel>
      <CustomTabPanel value={value} index={1}>
        <CorrelationAnalysis file_id={file_id} />
      </CustomTabPanel>
    </Box>
  )
}
