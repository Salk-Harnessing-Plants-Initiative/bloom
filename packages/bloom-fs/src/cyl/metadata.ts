// Using import to load the CommonJS modules
import path from "path";
import * as glob from "glob";
import * as fs from "fs";
import * as yaml from "js-yaml";
import Ajv from "ajv";
import { findConfigFile } from "typescript";
import { FileUploader } from "@salk-hpi/bloom-js/dist/types/file-uploader";
import { DataStore } from "@salk-hpi/bloom-js/dist/types/data-store";
import * as uuid from "uuid";
import * as xlsx from "xlsx";

export type CylImageMetadata = {
  species: string | undefined;
  experiment: string | undefined;
  wave_number: number | undefined;
  germ_day: number | undefined;
  germ_day_color: string | undefined;
  plant_age_days: number | undefined;
  date_scanned: string | undefined;
  device_name: string | undefined;
  plant_qr_code: string | undefined;
  frame_number: number | undefined;
  accession_name: string | undefined;
  phenotyper_name: string | undefined;
  phenotyper_email: string | undefined;
  scientist_name: string | undefined;
  scientist_email: string | undefined;

};

type CylMetadataSummary = {
  numImages: number;
  numPlants: number;
  plants: string[];
  accessions: string[];
  waveNumbers: number[];
  germDays: number[];
  germDayColors: string[];
  plantAges: number[];
  scanDates: string[];
  deviceNames: string[];
};

type CylFixedValues = CylImageMetadata;

type CylMetadataFormat = {
  path_format_string: string;
  fixed_values?: CylFixedValues;
  accession_info: {
    spreadsheet: string;
    sheet: string;
    qr_code_col: string;
    accession_col: string;
  };
};

const metadataFormatSchema = {
  $schema: "http://json-schema.org/draft-07/schema#",
  type: "object",
  properties: {
    path_format_string: {
      type: "string",
    },
    accession_info: {
      type: "object",
      properties: {
        spreadsheet: {
          type: "string",
        },
        sheet: {
          type: "string",
        },
        qr_code_col: {
          type: "string",
        },
        accession_col: {
          type: "string",
        },
      },
      required: ["spreadsheet", "sheet", "qr_code_col", "accession_col"],
      additionalProperties: false,
    },
    fixed_values: {
      type: "object",
      properties: {
        species: {
          type: "string",
        },
        experiment: {
          type: "string",
        },
        wave_number: {
          type: "number",
        },
        germ_day: {
          type: "number",
        },
        germ_day_color: {
          type: "string",
        },
        plant_age_days: {
          type: "number",
        },
        date_scanned: {
          type: "string",
        },
        device_name: {
          type: "string",
        },
        plant_qr_code: {
          type: "string",
        },
        frame_number: {
          type: "number",
        },
      },
      additionalProperties: false,
    },
  },
  required: ["path_format_string"],
  additionalProperties: false,
};

const fieldRegexps = {
  "<species>": "(?<species>[^/]+)",
  "<experiment>": "(?<experiment>[^/]+)",
  "<wave_number>": "(?<wave_number>\\d+)",
  "<germ_day>": "(?<germ_day>\\d+)",
  "<germ_day_color>": "(?<germ_day_color>[A-Za-z]+)",
  "<plant_age_days>": "(?<plant_age_days>\\d+)",
  "<date_scanned>": "(?<date_scanned>\\d{1,2}[-.]\\d{1,2}[-.]\\d{2,4})",
  "<device_name>": "(?<device_name>[A-Za-z0-9]+)",
  "<plant_qr_code>": "(?<plant_qr_code>[A-Za-z0-9_-]+)",
  "<frame_number>": "(?<frame_number>\\d+)",
};

export async function getImageMetadata(
  dir: string
): Promise<{ metadata: CylImageMetadata[]; paths: string[] }> {
  const { metadataFile, metadataRoot, rootToTargetDir } = findMetadataFile(dir);
  console.log(`Using metadata format file: ${metadataFile}\n`);
  const metadataFormat = yaml.load(fs.readFileSync(metadataFile, "utf8"));

  const ajv = new Ajv();
  const validate = ajv.compile(metadataFormatSchema);
  const valid = validate(metadataFormat);

  if (!valid) {
    console.error(`Invalid metadata format file: ${metadataFile}`);
    console.error("Errors: ");
    console.error(validate.errors);
    process.exit(1);
  }

  const { path_format_string, fixed_values, accession_info } =
    metadataFormat as CylMetadataFormat;
  const { spreadsheet, sheet, qr_code_col, accession_col } = accession_info;
  const qr_code_to_accession_name = await getPlantAccessions(
    path.join(metadataRoot, spreadsheet),
    sheet,
    qr_code_col,
    accession_col
  );

  const pathRegexp =
    "^" + replaceStrings(path_format_string, fieldRegexps) + "$";
  const globPattern = path.join(rootToTargetDir, "**/*.png");
  const parsedPaths = parsePaths(globPattern, pathRegexp, metadataRoot, true);

  const metadata = parsedPaths.map((p) => {
    if (!p.metadata) {
      // compute path of p.path relative to metadataRoot
      const relativePath = path.relative(metadataRoot, p.path);
      console.error("Error: Path did not match format string");
      console.error(`  Path: ${relativePath}`);
      console.error(`  Format string: ${path_format_string}`);
      console.error(
        `You may need to update the path_format_string field in ${metadataFile}`
      );
      process.exit(1);
    }

    // check that plant_qr_code is in accession_info
    if (
      p.metadata.plant_qr_code &&
      !qr_code_to_accession_name[p.metadata.plant_qr_code]
    ) {
      console.error(
        `Error: plant_qr_code ${p.metadata.plant_qr_code} (${p.path}) not found in accessions spreadsheet`
      );
      process.exit(1);
    }

    const accession_name = p.metadata.plant_qr_code
      ? qr_code_to_accession_name[p.metadata.plant_qr_code]
      : undefined;

    return {
      ...p.metadata,
      accession_name,
      ...fixed_values,
    } as CylImageMetadata;
  });

  const paths = parsedPaths.map((p) => p.path);

  return { metadata, paths };
}

function findMetadataFile(dir: string) {
  // search for metadata file in dir or ancestor directories
  let metadataFile = findConfigFile(dir, fs.existsSync, "cyl-metadata.yml");
  if (!metadataFile) {
    console.error(
      "Error: Could not find cyl-metadata.yml file in directory or ancestor directories"
    );
    process.exit(1);
  }
  const metadataRoot = path.dirname(metadataFile);
  const rootToTargetDir = path.relative(metadataRoot, dir);
  return { metadataFile, metadataRoot, rootToTargetDir };
}

type ReplacementMap = { [key: string]: string };

function replaceStrings(input: string, replacements: ReplacementMap): string {
  let result = input;
  for (const [search, replace] of Object.entries(replacements)) {
    result = result.split(search).join(replace);
  }
  return result;
}

export async function summarizeImageMetadata(
  metadata: CylImageMetadata[]
): Promise<CylMetadataSummary> {
  const numImages = metadata.length;
  const numPlants = uniqueDefined(metadata.map((m) => m.plant_qr_code)).length;
  const plants = uniqueDefined(metadata.map((m) => m.plant_qr_code));
  const accessions = uniqueDefined(metadata.map((m) => m.accession_name));
  const waveNumbers = uniqueDefined(metadata.map((m) => m.wave_number));
  const germDays = uniqueDefined(metadata.map((m) => m.germ_day));
  const germDayColors = uniqueDefined(metadata.map((m) => m.germ_day_color));
  const plantAges = uniqueDefined(metadata.map((m) => m.plant_age_days));
  const scanDates = uniqueDefined(metadata.map((m) => m.date_scanned)).map(
    (s) => s.split("T")[0]
  );
  const deviceNames = uniqueDefined(metadata.map((m) => m.device_name));
  return {
    numImages,
    numPlants,
    plants,
    accessions,
    waveNumbers,
    germDays,
    germDayColors,
    plantAges,
    scanDates,
    deviceNames,
  };
}

function uniqueDefined<T>(arr: (T | undefined)[]): T[] {
  return [...(new Set(arr.filter((x) => x !== undefined)) as Set<T>)].sort();
}

function parseFields(obj: { [x: string]: string }): CylImageMetadata {
  return {
    species: obj.species,
    experiment: obj.experiment,
    wave_number: obj.wave_number ? parseInt(obj.wave_number) : undefined,
    germ_day: obj.germ_day ? parseInt(obj.germ_day) : undefined,
    germ_day_color: obj.germ_day_color,
    plant_age_days: obj.plant_age_days
      ? parseInt(obj.plant_age_days)
      : undefined,
    date_scanned: obj.date_scanned ? parseDate(obj.date_scanned) : undefined,
    device_name: obj.device_name,
    plant_qr_code: obj.plant_qr_code,
    frame_number: obj.frame_number ? parseInt(obj.frame_number) : undefined,
    accession_name: undefined,
    phenotyper_name: obj.phenotyper_name,
    phenotyper_email: obj.phenotyper_email,
    scientist_name: obj.scientist_name,
    scientist_email: obj.scientist_email,
  };
}

function parsePaths(
  globPattern: string,
  regex: string,
  rootDir: string,
  makeRelativeToCwd: boolean = false
) {
  let paths = glob.sync(globPattern, { cwd: rootDir });
  let sortedPaths = paths.sort();
  let relativePrefix = path.relative(process.cwd(), rootDir);

  let parsedPaths = sortedPaths.map((p) => {
    const normalizedPath = path.normalize(p); // normalize path separators
    const match = normalizedPath.match(regex);
    const metadata = match ? parseFields({ ...match.groups }) : null;
    const finalPath = makeRelativeToCwd
      ? path.join(relativePrefix, normalizedPath)
      : normalizedPath;

    return {
      path: finalPath,
      metadata: metadata,
    };
  });

  return parsedPaths;
}

function parseDate(dateString: string): string {
  let [month, day, year] = dateString.split(/[-.]/).map(Number);
  // Handle 2-digit years
  if (year < 100) {
    year += year < 50 ? 2000 : 1900;
  }
  // Using UTC ensures consistent Date objects no matter the computer's timezone
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.toISOString();
}

export function printSummary(summary: CylMetadataSummary) {
  const {
    numPlants,
    numImages,
    waveNumbers,
    germDays,
    germDayColors,
    plantAges,
    scanDates,
    deviceNames,
  } = summary;
  console.log(`Number of plants: ${numPlants}`);
  console.log(`Number of images: ${numImages}`);
  console.log(`Wave numbers: ${waveNumbers.join(", ") || "none"}`);
  console.log(`Germ days: ${germDays.join(", ") || "none"}`);
  console.log(`Germ day colors: ${germDayColors.join(", ") || "none"}`);
  console.log(`Plant ages: ${plantAges.join(", ") || "none"}`);
  console.log(`Scan dates: ${scanDates.join(", ") || "none"}`);
  console.log(`Device names: ${deviceNames.join(", ") || "none"}`);
}

/**
 * Concurrently maps an array to a new array using an asynchronous function.
 * @param array The input array to map.
 * @param nWorkers The maximum number of concurrent workers to use.
 * @param asyncFunc The asynchronous function to apply to each element of the array.
 * @returns A new array with the results of applying the asyncFunc to each element of the input array.
 */
export async function concurrentMap<T, U>(
  array: T[],
  nWorkers: number,
  asyncFunc: (t: T, index: number) => Promise<U>
) {
  let result: U[] = [];
  let index = 0;

  // Each "worker" will process items from the array in a loop
  const workers = Array.from({ length: nWorkers }, async () => {
    while (index < array.length) {
      const currentIndex = index++;
      result[currentIndex] = await asyncFunc(array[currentIndex], currentIndex);
    }
  });

  // Wait for all the workers to finish.
  await Promise.all(workers);

  return result;
}

export async function uploadImages(
  imagePaths: string[],
  metadata: CylImageMetadata[],
  uploader: FileUploader,
  store: DataStore,
  opts?: {
    nWorkers?: number;
    pngCompression?: number;
    before?: (index: number, m: CylImageMetadata) => void;
    result?: (
      index: number,
      m: CylImageMetadata,
      created: number | null,
      error: any
    ) => void;
  }
) {
  const { before, result } = opts || {};
  const nWorkers = opts?.nWorkers || 4;
  const pngCompression = opts?.pngCompression || 9;
  await concurrentMap(metadata, nWorkers, async (m, i) => {
    if (before) {
      before(i, metadata[i]);
    }
    const { created, error } = await uploadImage(
      imagePaths[i],
      metadata[i],
      uploader,
      store,
      { pngCompression }
    );
    if (result) {
      result(i, metadata[i], created, error);
    }
  });
}

async function uploadImage(
  imagePath: string,
  metadata: CylImageMetadata,
  uploader: FileUploader,
  store: DataStore,
  opts?: { pngCompression?: number }
) {
  const pngCompression = opts?.pngCompression || 9;
  // check that imagePath exists
  if (!fs.existsSync(imagePath)) {
    return { created: null, error: new Error("imagePath does not exist") };
  }
  const { created, error: dbError } = await store.insertImageMetadata(metadata);
  // if created, then attempt to upload image
  let uploadError;
  if (created !== null && dbError === null) {
    const bucket = "images";
    const dir = "cyl-images";
    const filename = `cyl-image_${created}_${uuid.v4()}.png`;
    let targetPath = `${dir}/${filename}`;
    ({ error: uploadError } = await uploader.uploadImage(
      imagePath,
      targetPath,
      bucket,
      { pngCompression }
    ));
    // if upload succeeds, set image.object_path and image.status = 'SUCCESS'
    if (uploadError === null) {
      const { error } = await store.updateImageMetadata(created, {
        object_path: targetPath,
        status: "SUCCESS",
      });
    } else {
      const { error } = await store.updateImageMetadata(created, {
        status: "ERROR",
      });
    }
  } else {
    uploadError = null;
  }

  return { created, error: dbError || uploadError };
}

export async function getPlantAccessions(
  xlsxPath: string,
  sheetName: string,
  qrCodeCol: string,
  accessionCol: string
) {
  const workbook = xlsx.readFile(xlsxPath);
  const sheet = workbook.Sheets[sheetName];
  const rows = xlsx.utils.sheet_to_json(sheet, { header: 1 }) as string[][];
  const qrCodeColIndex = rows[0].indexOf(qrCodeCol);
  const accessionColIndex = rows[0].indexOf(accessionCol);
  if (qrCodeColIndex === -1) {
    throw new Error(`Could not find column ${qrCodeCol}`);
  }
  if (accessionColIndex === -1) {
    throw new Error(`Could not find column ${accessionCol}`);
  }
  const qr_codes = rows.slice(1).map((row) => row[qrCodeColIndex]);
  const accessions = rows.slice(1).map((row) => row[accessionColIndex]);

  const qr_code_to_accession_name: { [key: string]: string } = {};
  qr_codes.forEach((qr_code, i) => {
    qr_code_to_accession_name[qr_code] = accessions[i];
  });

  return qr_code_to_accession_name;
}
