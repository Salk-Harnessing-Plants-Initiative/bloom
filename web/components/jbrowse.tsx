"use client";

import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import { openPicks, type BrowserSession } from "@/lib/jbrowse/browser-session";
import { launchPlan, launchTarget, picksToOpen } from "@/lib/jbrowse/launch";
import type { PicksPlan } from "@/lib/jbrowse/several-genes";

// Lazy load JBrowse only on client side
let createViewState: any;
let JBrowseApp: any;
let getEnv: any;

type ViewModel = any;

let jbrowseModules: Promise<[typeof import("@jbrowse/react-app2"), typeof import("@jbrowse/core/util")]> | null = null;
/** JBrowse's modules, imported once and shared by every load. */
const importJBrowse = () =>
  (jbrowseModules ??= Promise.all([import("@jbrowse/react-app2"), import("@jbrowse/core/util")]).catch((err) => {
    jbrowseModules = null; // a retry imports again
    throw err;
  }));

export interface JBrowseProps {
  loaded: LoadedBrowserConfig;
  picks: PicksPlan | null;
  /** Given JBrowse's session once it is made. */
  onSession?: (session: BrowserSession) => void;
  /** Called whenever the session changes. */
  onChange?: () => void;
}

function JBrowse({ loaded, picks, onSession, onChange }: JBrowseProps) {
  const onSessionRef = useRef(onSession);
  const onChangeRef = useRef(onChange);
  onSessionRef.current = onSession;
  onChangeRef.current = onChange;
  const searchParams = useSearchParams();
  const plan = launchPlan(launchTarget(searchParams), loaded);
  // Read when the browser is made: a later address change (picks opened in place) keeps it.
  const launch = useRef({ plan, picks });
  launch.current = { plan, picks };
  const config = loaded.config;

  const [viewState, setViewState] = useState<ViewModel>();
  const [isLoading, setIsLoading] = useState(true);
  const [loadingStatus, setLoadingStatus] = useState("Initializing JBrowse...");
  const [error, setError] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    let live = true;
    // Dynamically import JBrowse modules only on client side
    const loadJBrowse = async () => {
      try {
        setError(null);
        setLoadingStatus("Loading JBrowse libraries...");
        
        const [jbrowseReactApp, jbrowseCore] = await importJBrowse();

        if (!live) return;
        createViewState = jbrowseReactApp.createViewState;
        JBrowseApp = jbrowseReactApp.JBrowseApp;
        getEnv = jbrowseCore.getEnv;
        
        setLoadingStatus("Initializing genome browser...");
        const state = createViewState({ config, onChange: () => onChangeRef.current?.() });
        const { pluginManager } = getEnv(state);
        setViewState(state);
        onSessionRef.current?.(state.session);
        setLaunchError(null);

        const { plan: target, picks: picked } = launch.current;
        if (target) {
          const { assembly, loc, tracks } = target;
          setLoadingStatus(`Loading ${assembly}${loc ? ` at ${loc}` : ""}...`);
          const places = picksToOpen(target, picked);
          // Without a location JBrowse shows the whole assembly.
          const opened = places
            ? openPicks(state.session, assembly, places, tracks)
            : Promise.resolve(
                pluginManager.evaluateAsyncExtensionPoint("LaunchView-LinearGenomeView", {
                  tracks,
                  loc,
                  assembly,
                  session: state.session,
                }),
              );
          opened.catch((err: unknown) => {
            if (live) {
              setLaunchError(`${assembly} could not be opened${loc ? ` at ${loc}` : ""}: ${err instanceof Error ? err.message : String(err)}`);
            }
          });
        }

        setIsLoading(false);
      } catch (err) {
        if (!live) return;
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
    return () => {
      live = false;
    };
  }, [config, retryCount]);

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

