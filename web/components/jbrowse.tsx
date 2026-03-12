"use client";

import React, { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

// Lazy load JBrowse only on client side
let createViewState: any;
let JBrowseApp: any;
let getEnv: any;

type ViewModel = any;

function JBrowse() {
  const searchParams = useSearchParams();
  const reference = searchParams.get("reference"); 
  const gene = searchParams.get("gene");

  const [viewState, setViewState] = useState<ViewModel>();
  const [isLoading, setIsLoading] = useState(true);
  const [loadingStatus, setLoadingStatus] = useState("Initializing JBrowse...");
  const [config, setConfig] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    // Dynamically import JBrowse modules only on client side
    const loadJBrowse = async () => {
      try {
        setError(null);
        setLoadingStatus("Loading JBrowse libraries...");
        
        const [jbrowseReactApp, jbrowseCore] = await Promise.all([
          import("@jbrowse/react-app"),
          import("@jbrowse/core/util"),
        ]);

        createViewState = jbrowseReactApp.createViewState;
        JBrowseApp = jbrowseReactApp.JBrowseApp;
        getEnv = jbrowseCore.getEnv;
        
        setLoadingStatus("Loading genome assemblies (98 genomes)...");
        // Load config separately to show progress
        const configModule = await import("./config");
        const loadedConfig = configModule.default;
        setConfig(loadedConfig);

        setLoadingStatus("Initializing genome browser...");
        console.log(loadedConfig);
        const state = createViewState({
          config: loadedConfig,
        });
        const { pluginManager } = getEnv(state);
        setViewState(state);

        if (reference && gene) {
          setLoadingStatus(`Loading ${reference} at ${gene}...`);
          pluginManager.evaluateAsyncExtensionPoint(
            'LaunchView-LinearGenomeView',
            {
              tracks: [],
              loc: gene,
              assembly: reference,
              session: state.session,
            },
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
  }, [reference, gene, retryCount]);

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-4">
        <div className="text-center space-y-4">
          <div className="text-red-600">{error}</div>
          {retryCount >= 3 && (
            <button 
              onClick={() => window.location.reload()} 
              className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
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
          <div className="w-16 h-16 border-4 border-gray-300 border-t-blue-500 rounded-full animate-spin mx-auto"></div>
          <div className="text-gray-700 text-lg font-medium">{loadingStatus}</div>
          {loadingStatus.includes("98 genomes") && (
            <div className="text-sm text-gray-500 max-w-md">
              Loading comprehensive genome database. First load may take a few seconds...
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="p-4">
      <JBrowseApp viewState={viewState} />
    </div>
  );
}

export default JBrowse;

