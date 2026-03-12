"use client";

import { useState } from "react";

export default function ExperimentDescription({
  experiment,
}: {
  experiment: any;
}) {
  const [shorten, setShorten] = useState(true);

  return (
    <div className="mb-8 w-[600px]">
      {/* <div className="text-xs font-bold mb-2 text-stone-500">Description</div> */}
      <span className="text-sm text-stone-500">
        {experiment?.description
          ? shorten && experiment?.description.length > 150
            ? experiment.description.slice(0, 150) + "... "
            : experiment.description + " "
          : "No description provided."}
      </span>
      {experiment?.description && experiment?.description.length > 150 ? (
        <button
          className="text-sm text-lime-700 hover:underline"
          onClick={() => setShorten(!shorten)}
        >
          {shorten ? "Read more" : "Read less"}
        </button>
      ) : null}
    </div>
  );
}
