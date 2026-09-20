"use client";

/** Where a page in the app lands when it throws: what failed, and a way to try again. */
export default function AppError({
  error,
  reset,
  retry,
}: {
  error: Error & { digest?: string };
  reset: () => void;
  /** Reloads the page from the server, then clears the error; passed by newer Next. */
  retry?: () => void;
}) {
  // A server error carries a digest, and in production Next replaces its message with its own.
  const what = error.digest ? "The server could not build this page." : error.message;
  return (
    <div
      role="alert"
      className="max-w-xl mx-auto mt-16 rounded-lg border border-rose-200 bg-rose-50 p-6 text-sm text-rose-900"
    >
      <p className="font-medium">Something went wrong on this page.</p>
      {what && <p className="mt-2 text-rose-800">{what}</p>}
      {error.digest && <p className="mt-1 text-xs text-rose-700">Reference: {error.digest}</p>}
      <button
        type="button"
        onClick={() => (retry ?? reset)()}
        className="mt-4 rounded-md border border-rose-300 bg-white px-3 py-1.5 text-rose-800 hover:bg-rose-100"
      >
        Try again
      </button>
    </div>
  );
}
