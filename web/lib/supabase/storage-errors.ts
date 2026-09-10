// Telling a missing object apart from a lookup that failed. Not specific to any
// one kind of video: both the cyl and gravi modules gate on this answer.

// Storage reports a missing object as an error, so only a genuine not-found
// counts as "absent" — anything else (permissions, gateway, timeout) is unknown.
//
// `statusCode` is the service's own code and is a *string*; `status` is the HTTP
// status, and Storage answers a missing object with 400, not 404, so that code
// is what decides and the wording is only the fallback.
export function isNotFound(error: {
  message?: string;
  status?: number;
  statusCode?: string;
}): boolean {
  if (error.statusCode === "404" || error.status === 404) return true;
  return /not[_ ]?found|does not exist|no such/i.test(error.message ?? "");
}
