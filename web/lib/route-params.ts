// Reading path parameters that go on to be interpolated into an upstream URL.
// Not specific to any one route: the cyl and gravi video routes both build an
// upstream path out of an id taken from their own.

// A route param that is safe to interpolate into the upstream URL. Anything
// non-integer is rejected rather than escaped: these land in a path segment, so
// a value like "1/../../health" would otherwise retarget the request.
export function parseId(value: string | undefined | null): number | null {
  if (typeof value !== "string" || !/^\d+$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}
