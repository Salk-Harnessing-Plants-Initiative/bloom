// Where a plate's time-lapse video lives. Pure strings, no Supabase client and
// no `next/headers`, so a client component can import this.
//
// services/workflows/plate_video_path.py builds the same key from the encoder
// side and scripts/render_plate_videos.py already wrote objects under it. All
// three are pinned together by tests/unit/test_plate_video_path_agreement.py.

export const GRAVISCAN_VIDEOS_BUCKET = "graviscan-videos";
export const GRAVISCAN_IMAGES_BUCKET = "graviscan-images";

// A plate id is free text from the scanner and becomes a path segment, so it is
// checked against a whitelist rather than escaped — one rule instead of one per
// destination. No leading dot, so `..` cannot form. Kept byte-identical to the
// Python copy; the agreement test compares the literals.
export const PLATE_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$";

export function isValidPlateId(plateId: string): boolean {
  return new RegExp(PLATE_ID_PATTERN).test(plateId);
}

// A plate with no wave still needs a distinct segment: an empty one would
// collide with the experiment's own directory level.
export function waveSegment(waveNumber: number | null): string | null {
  if (waveNumber === null) return "wave-none";
  if (!Number.isInteger(waveNumber) || waveNumber < 0) return null;
  return `wave-${waveNumber}`;
}

// Null when any part is unusable, so a bad plate id cannot become a path.
// Callers treat that as "this plate has no video" rather than guessing a key.
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
