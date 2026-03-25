export default function OrthofinderPage() {
  return (
    <div className="w-full h-[calc(100vh-8rem)] flex flex-col">
      <div className="flex items-center justify-between px-4 py-2 bg-white rounded-lg border border-stone-200 mb-3 text-sm">
        <span className="text-neutral-600">
          Maintained by Nolan Hartwick <span className="font-bold text-neutral-500">(Michael Lab)</span>
        </span>
        <a href="mailto:nhartwick@salk.edu" className="text-lime-700 hover:text-lime-800 hover:underline transition-colors">
          nhartwick@salk.edu
        </a>
      </div>
      <iframe
        src="https://resources.michael.salk.edu/misc/hpi_orthobrowser/index.html"
        className="w-full flex-grow border-0 rounded-lg"
        title="HPI OrthoBrowser"
        allowFullScreen
      />
    </div>
  );
}
