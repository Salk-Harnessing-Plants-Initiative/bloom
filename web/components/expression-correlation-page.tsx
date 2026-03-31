import * as React from 'react';
import Box from '@mui/material/Box';
import CorrelationAnalysis from "./expression-correlation-analysis";

export default function BasicTabs({ file_id }: { file_id: number }) {
  return (
    <Box sx={{ width: '100%' }}>
      <CorrelationAnalysis file_id={file_id} />
    </Box>
  );
}
