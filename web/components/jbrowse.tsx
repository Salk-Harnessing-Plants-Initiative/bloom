"use client";

import React, { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import { launchPlan, launchTarget } from "@/lib/jbrowse/launch";

// Lazy load JBrowse only on client side
let createViewState: any;
let JBrowseApp: any;
let getEnv: any;

type ViewModel = any;

function JBrowse({ loaded }: { loaded: LoadedBrowserConfig }) {
  const searchParams = useSearchParams();
  const plan = launchPlan(launchTarget(searchParams), loaded);
  const reference = plan?.assembly;
  const loc = plan?.loc;
  const tracks = plan?.tracks.join("\n") ?? "";
  const config = loaded.config;

  const [viewState, setViewState] = useState<ViewModel>();
  const [isLoading, setIsLoading] = useState(true);
  const [loadingStatus, setLoadingStatus] = useState("Initializing JBrowse...");
  const [error, setError] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    // Dynamically import JBrowse modules only on client side
    const loadJBrowse = async () => {
      try {
        setError(null);
        setLoadingStatus("Loading JBrowse libraries...");
        
        const [jbrowseReactApp, jbrowseCore] = await Promise.all([
          import("@jbrowse/react-app2"),
          import("@jbrowse/core/util"),
        ]);

        createViewState = jbrowseReactApp.createViewState;
        JBrowseApp = jbrowseReactApp.JBrowseApp;
        getEnv = jbrowseCore.getEnv;
        
        setLoadingStatus("Initializing genome browser...");
        const state = createViewState({ config });
        const { pluginManager } = getEnv(state);
        setViewState(state);
        setLaunchError(null);

        if (reference) {
          setLoadingStatus(`Loading ${reference}${loc ? ` at ${loc}` : ""}...`);
          // Without a location JBrowse shows the whole assembly.
          Promise.resolve(
            pluginManager.evaluateAsyncExtensionPoint("LaunchView-LinearGenomeView", {
              tracks: tracks ? tracks.split("\n") : [],
              loc,
              assembly: reference,
              session: state.session,
            }),
          ).catch((err: unknown) =>
            setLaunchError(`${reference} could not be opened${loc ? ` at ${loc}` : ""}: ${err instanceof Error ? err.message : String(err)}`),
          );
        }

        setIsLoading(false);
      } catch (err) {
        console.error("Failed to load JBrowse:", err);
        const errorMessage = err instanceof Error ? err.message : String(err);
        
        // Check if it's the HMR CSS module issue
        if (errorMessage.includes('module factory is not available') || errorMessage.includes('HMR')) {
          setError("Loading JBrowse... (recovering from hot reload)");
          // Retry after a short delay if it's an HMR issue and we haven't retried too many times
          if (retryCount < 3) {
            setTimeout(() => {
              setRetryCount(retryCount + 1);
              setIsLoading(true);
            }, 1000);
          } else {
            setError("Please refresh the page to load JBrowse.");
            setIsLoading(false);
          }
        } else {
          setError(`Failed to load JBrowse: ${errorMessage}`);
          setIsLoading(false);
        }
      }
    };

    loadJBrowse();
  }, [config, reference, loc, tracks, retryCount]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-4">
        <div className="text-center space-y-4">
          <div className="text-red-600">{error}</div>
          {retryCount >= 3 && (
            <button 
              onClick={() => window.location.reload()} 
              className="px-4 py-2 bg-lime-700 text-white rounded hover:bg-lime-800"
            >
              Refresh Page
            </button>
          )}
        </div>
      </div>
    );
  }

  if (isLoading || !viewState || !JBrowseApp) {
    return (
      <div className="flex items-center justify-center h-full p-4">
        <div className="text-center space-y-4">
          <div className="w-16 h-16 border-4 border-stone-300 border-t-lime-700 rounded-full animate-spin mx-auto"></div>
          <div className="text-stone-700 text-lg font-medium">{loadingStatus}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4">
      {launchError && <p className="pb-2 text-sm text-red-700">{launchError}</p>}
      <JBrowseApp viewState={viewState} />
    </div>
  );
}

export default JBrowse;

