import { createObjectCsvWriter } from 'csv-writer'
import { parse } from 'csv-parse/sync'

import fs from 'fs'

export async function saveToCSV(
  data: any[],
  csv_path: string,
  header: { id: string; title: string }[]
) {
  const csvWriter = createObjectCsvWriter({
    path: csv_path,
    header: header,
  })
  await csvWriter.writeRecords(data)
}

export async function processCSV(csv_path: string, callback: (row: any) => Promise<void>) {
  const content = fs.readFileSync(csv_path)
  const records = parse(content, { columns: true })
  for (const record of records) {
    await callback(record)
  }
}
