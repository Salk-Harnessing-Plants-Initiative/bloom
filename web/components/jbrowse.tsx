"use client";

import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import { freshBrowserConfig } from "@/app/app/jbrowse/actions";
import { refreshDue } from "@/lib/jbrowse/bloom-genomes";
import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes-server";
import { launchTarget, type LaunchTarget } from "@/lib/jbrowse/launch";

// Lazy load JBrowse only on client side
let createViewState: any;
let JBrowseApp: any;
let getEnv: any;

type ViewModel = any;

/** How often the page checks whether its signed file URLs need refreshing. */
const REFRESH_CHECK_MS = 60 * 1000;

interface Opening extends LaunchTarget {
  tracks: string[];
}

/** Where the open linear genome view is and which tracks it shows; null with none open. */
function openedAt(state: ViewModel): Opening | null {
  const view = state?.session?.views?.find((v: any) => v.type === "LinearGenomeView");
  const assembly = view?.assemblyNames?.[0];
  const loc = view?.initialized ? view.coarseVisibleLocStrings : undefined;
  if (!assembly || !loc) return null;
  return { assembly, loc, tracks: view.tracks.map((t: any) => t.configuration.trackId) };
}

function JBrowse({ initial }: { initial: LoadedBrowserConfig }) {
  const searchParams = useSearchParams();
  const [loaded, setLoaded] = useState(initial);
  // When the signed URLs expire, on this browser's clock (the server's may differ).
  const [deadline, setDeadline] = useState(() =>
    initial.validForMs === null ? null : Date.now() + initial.validForMs,
  );
  const [reopenAt, setReopenAt] = useState<Opening | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const viewRef = useRef<ViewModel>(undefined);
  const refreshing = useRef(false);

  const linked = launchTarget(searchParams);
  // A Bloom genome opened by link shows its genes; a refresh reopens what was shown.
  const wanted: Opening | null =
    reopenAt ??
    (linked
      ? { ...linked, tracks: loaded.bloomGenomes.includes(linked.assembly) ? [`${linked.assembly}-genes`] : [] }
      : null);
  const target = wanted && loaded.config.assemblies.some((a) => a.name === wanted.assembly) ? wanted : null;
  const reference = target?.assembly;
  const gene = target?.loc;
  const tracks = target?.tracks.join("\n") ?? "";
  const config = loaded.config;

  // Fetch fresh signed URLs shortly before they expire, and reopen where the viewer was.
  useEffect(() => {
    if (deadline === null) return;
    const check = async () => {
      if (refreshing.current || !refreshDue(deadline, Date.now())) return;
      refreshing.current = true;
      try {
        const fresh = await freshBrowserConfig();
        if (fresh.error) throw new Error(fresh.error);
        setRefreshError(null);
        setReopenAt(openedAt(viewRef.current));
        setDeadline(fresh.validForMs === null ? null : Date.now() + fresh.validForMs);
        setLoaded(fresh);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setRefreshError(`Bloom's genome files could not be refreshed (${message}); they stop loading when their links expire.`);
      } finally {
        refreshing.current = false;
      }
    };
    const timer = setInterval(check, REFRESH_CHECK_MS);
    document.addEventListener("visibilitychange", check);
    check();
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
    };
  }, [deadline]);

  const [viewState, setViewState] = useState<ViewModel>();
  const [isLoading, setIsLoading] = useState(true);
  const [loadingStatus, setLoadingStatus] = useState("Initializing JBrowse...");
  const [error, setError] = useState<string | null>(null);
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
        viewRef.current = state;
        setViewState(state);

        if (reference && gene) {
          setLoadingStatus(`Loading ${reference} at ${gene}...`);
          pluginManager.evaluateAsyncExtensionPoint(
            'LaunchView-LinearGenomeView',
            {
              tracks: tracks ? tracks.split("\n") : [],
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
  }, [config, reference, gene, tracks, retryCount]);

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
        </div>
      </div>
    );
  }

  return (
    <div className="p-4">
      {refreshError && <p className="pb-2 text-sm text-red-700">{refreshError}</p>}
      <JBrowseApp viewState={viewState} />
    </div>
  );
}

export default JBrowse;

