// Where a plate's time-lapse video lives. Pure strings, so a client component
// can import this. The Python copy in services/workflows/plate_video_path.py
// must stay identical; tests/unit/test_plate_video_path_agreement.py checks it.

export const GRAVISCAN_VIDEOS_BUCKET = "graviscan-videos";
export const GRAVISCAN_IMAGES_BUCKET = "graviscan-images";

// A plate id is free text and becomes a path segment, so it is whitelisted
// rather than escaped. No leading dot, so `..` cannot form.
export const PLATE_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$";

export function isValidPlateId(plateId: string): boolean {
  return new RegExp(PLATE_ID_PATTERN).test(plateId);
}

// A plate with no wave still needs a segment; an empty one would collide with
// the experiment's own level.
export function waveSegment(waveNumber: number | null): string | null {
  if (waveNumber === null) return "wave-none";
  if (!Number.isInteger(waveNumber) || waveNumber < 0) return null;
  return `wave-${waveNumber}`;
}

// Null when any part is unusable, so a bad plate id cannot become a path.
export function plateVideoPath(
  experimentId: number,
  waveNumber: number | null,
  plateId: string,
): string | null {
  if (!Number.isInteger(experimentId) || experimentId <= 0) return null;
  if (!isValidPlateId(plateId)) return null;

  const wave = waveSegment(waveNumber);
  if (wave === null) return null;

  return `${experimentId}/${wave}/${plateId}.mp4`;
}
