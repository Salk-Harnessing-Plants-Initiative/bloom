/** Paths the middleware lets through without a login. The genome browser's file route checks
 * the login itself and answers 401, so JBrowse gets an error instead of the login page's HTML. */
const SIGNED_OUT_PREFIXES = [
  "/login",
  "/forgot-password",
  "/reset-password",
  "/verify",
  "/auth",
  "/error",
  "/api", // internal routes
  "/app/jbrowse/files/",
];

export function passesWithoutLogin(path: string): boolean {
  return SIGNED_OUT_PREFIXES.some((prefix) => path.startsWith(prefix));
}
