import * as fs from "fs/promises";

export async function initCylMetadata(
  dir: string,
  species: string,
  experiment: string,
  spreadsheet: string,
  sheet: string,
  qrCodeCol: string,
  accessionCol: string
) {
  const outputFile = `${dir}/cyl-metadata.yml`;

  await fs.writeFile(
    outputFile,
    initMetadata(
      dir,
      species,
      experiment,
      spreadsheet,
      sheet,
      qrCodeCol,
      accessionCol
    )
  );

  console.log();
  console.log(`Initialized ${outputFile}`);
  console.log();
  console.log(`Edit ${outputFile} to reflect the correct metadata format.`);
  console.log(`
Then, run any of the following commands.

To parse and print metadata:  bloom cyl preview ${dir}
To validate the metadata:     bloom cyl validate ${dir}
To upload the scans:          bloom cyl upload ${dir}

Note that these commands can also be run on subdirectories of ${dir}.
  `);
}

function initMetadata(
  dir: string,
  species: string,
  experiment: string,
  spreadsheet: string,
  sheet: string,
  qrCodeCol: string,
  accessionCol: string
) {
  return `# cyl-metadata.yml
#
# This file describes the metadata for a set of cylinder scans.
#
# It can be created with the 'bloom cyl init DIR' command,
# and must be edited to reflect the correct metadata format.
#
# Other commands that depend on the metadata.yml file include:
#
#  bloom cyl preview DIR
#  bloom cyl validate DIR
#  bloom cyl upload DIR
#
# Note that these commands can also be run on subdirectories of DIR.


path_format_string: Images/W<wave_number>/GermDay<germ_day><germ_day_color>/Day<plant_age_days>_<date_scanned><device_name>/<plant_qr_code>/<frame_number>.png


# 'path_format_string' is a string that describes how to extract metadata
# from the filepath of each cylinder scan below this directory.
#
# For example, if there is a cylinder scan image at the following path:
#
#   Images/W1/GermDay1Purple/Day1_06-26-2023FastScanner/XKGMRWEOIDFM/1.png"
#
# then we can set path_format_string to the following:
#
# path_format_string: Images/W<wave_number>/GermDay<germ_day><germ_day_color>/Day<plant_age_days>_<date_scanned><device_name>/<plant_qr_code>/<frame_number>.png
#
# Note that '>' starts a multi-line string in YAML (newlines are ignored).
# It is not necessary to use a multi-line string, but it makes the format
# string easier to read.
#
# path_format_string can contain the following field names:
#
# <wave_number> - wave number
# <germ_day> - germination day of the germination group
# <germ_day_color> - color code of the germination group
# <plant_age_days> - age of the plant in days
# <date_scanned> - date the scan was taken
# <device_name> - name of the device that took the scan
# <plant_qr_code> - plant QR code identifier
# <frame_number> - frame number of the image in the scan
#
# Note that any fields that are not present in path_format_string will be
# left blank in the metadata. However, they can be specified manually in
# the fixed_values section below.


fixed_values:
  species: "${species}"
  experiment: "${experiment}"


# 'fixed_values' is dictionary that specifies the metadata fields that are
# fixed for all scans below this directory.
#
# For example:
#
#   species: "Canola"
#   experiment": "diversity-screen"
#
# would specify that all scans below this directory are in the Canola
# experiment called "diversity-screen".
#
# All field strings that are valid for path_format_string are also valid
# keys for fixed_values. In this way, any fields that do not appear in the
# filepaths can be specied manually by including them in fixed_values.

# Optional: uncomment the section below.

# replace_values:
#   device_name:
#     "Fast": "FastScanner"
#     "Slow": "SlowScanner"

# replace_values allows you to correct individual values to be consistent with
# values in the database.


accession_info:
  spreadsheet: ${spreadsheet}
  sheet: ${sheet}
  qr_code_col: ${qrCodeCol}
  accession_col: ${accessionCol}

# 'accession_info' contains spreadsheet information for mapping plant
# QR codes to accession IDs.

`;
}
