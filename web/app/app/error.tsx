"use client";

/** Where a page in the app lands when it throws: what failed, and a way to try again. */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div
      role="alert"
      className="max-w-xl mx-auto mt-16 rounded-lg border border-rose-200 bg-rose-50 p-6 text-sm text-rose-900"
    >
      <p className="font-medium">Something went wrong on this page.</p>
      {error.message && <p className="mt-2 text-rose-800">{error.message}</p>}
      <button
        type="button"
        onClick={() => reset()}
        className="mt-4 rounded-md border border-rose-300 bg-white px-3 py-1.5 text-rose-800 hover:bg-rose-100"
      >
        Try again
      </button>
    </div>
  );
}
