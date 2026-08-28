export function withShortTimeZoneName(
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormatOptions {
  return {
    ...options,
    timeZoneName: "short",
  };
}
