/** What the middleware does with a signed-out request: public paths pass, the genome browser's
 * data paths answer 401 (JBrowse fetches them, so the login page's HTML would be read as data),
 * and every other path goes to the login page. The data routes also check the login themselves. */
const PUBLIC_PREFIXES = [
  "/login",
  "/forgot-password",
  "/reset-password",
  "/verify",
  "/auth",
  "/error",
  "/api", // internal routes
];

/** The genome browser's data routes: build files, gene density and single-cell expression. */
const DATA_ROUTES = ["/app/jbrowse/files", "/app/jbrowse/density", "/app/jbrowse/expression"];

/** The body of a signed-out data request's 401. */
export const SIGNED_OUT_DATA_BODY = "Sign in to read genome data.";

export type SignedOutAnswer = "pass" | "401" | "login";

export function signedOutAnswer(path: string): SignedOutAnswer {
  if (PUBLIC_PREFIXES.some((prefix) => path.startsWith(prefix))) return "pass";
  if (DATA_ROUTES.some((route) => path === route || path.startsWith(`${route}/`))) return "401";
  return "login";
}
