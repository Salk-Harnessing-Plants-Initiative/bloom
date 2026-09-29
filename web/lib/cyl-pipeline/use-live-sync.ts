"use client";

/**
 * The live views' shared sync loop (spec: "Live views synchronise from
 * Realtime without polling").
 *
 * - One channel per mounted instance, on a unique topic: StrictMode
 *   double-mounts on the singleton browser client, and a reused topic would
 *   hand the second mount the first mount's channel.
 * - Every SUBSCRIBED goes through the resync scheduler (leading plus
 *   trailing). Nothing else here fetches.
 * - Events that arrive during a snapshot fetch are buffered and replayed on
 *   top of it. Only the latest fetch's result is applied.
 * - State lives in a ref, so an event handler can read what it just applied
 *   (to decide on a one-off lookup) without side effects inside a React
 *   updater, which StrictMode calls twice.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { beginFetch, endFetch, failFetch, receive, synced, type Change, type Synced } from "./realtime-reducer";
import { createResyncScheduler, nextConnectionState, type ConnectionState } from "./resync-scheduler";

export interface LiveBinding {
  table: string;
  event: "INSERT" | "UPDATE" | "*";
  filter?: string;
}

export interface LiveSyncOptions<V> {
  /** Prefix of the per-instance channel topic. */
  topic: string;
  bindings: LiveBinding[];
  initial: V;
  /** Read a fresh snapshot. Called on each resync and on refresh(). */
  snapshot: () => Promise<V>;
  apply: (view: V, change: Change<unknown>) => V;
  /** Called after each event has been received (applied, or buffered while fetching). */
  onEvent?: (change: Change<unknown>, view: V) => void;
  /** Called after a snapshot has been applied. */
  onSnapshot?: (view: V) => void;
}

export interface LiveSync<V> {
  view: V;
  connection: ConnectionState;
  /** The last snapshot's error message, cleared by the next successful one. */
  error: string | null;
  fetching: boolean;
  refresh: () => void;
  /** Change the held view outside a snapshot (for example, "load older"). */
  update: (fn: (view: V) => V) => void;
  /** The current view, for event handlers. */
  current: () => V;
}

let topicSeq = 0;

export function useLiveSync<V>(options: LiveSyncOptions<V>): LiveSync<V> {
  const opts = useRef(options);
  opts.current = options;

  const store = useRef<Synced<V, Change<unknown>>>(synced(options.initial));
  const [, render] = useReducer((n: number) => n + 1, 0);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const mounted = useRef(true);

  const apply = (view: V, change: Change<unknown>) => opts.current.apply(view, change);

  const refetch = useCallback(async () => {
    const gen = ++generation.current;
    store.current = beginFetch(store.current);
    render();
    try {
      const snap = await opts.current.snapshot();
      if (!mounted.current || gen !== generation.current) return;
      store.current = endFetch(store.current, snap, apply);
      setError(null);
      opts.current.onSnapshot?.(store.current.view);
    } catch (e) {
      if (!mounted.current || gen !== generation.current) return;
      store.current = failFetch(store.current, apply);
      setError(e instanceof Error ? e.message : String(e));
    }
    render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const bindingsKey = JSON.stringify(options.bindings);
  useEffect(() => {
    mounted.current = true;
    const client = createClientSupabaseClient();
    const scheduler = createResyncScheduler(() => void refetch());
    let channel = client.channel(`${opts.current.topic}:${++topicSeq}`);
    for (const b of JSON.parse(bindingsKey) as LiveBinding[]) {
      channel = channel.on(
        // realtime-js overloads `on` per event type; this is the postgres_changes form.
        "postgres_changes" as never,
        { event: b.event, schema: "public", table: b.table, ...(b.filter ? { filter: b.filter } : {}) } as never,
        ((payload: { table: string; eventType: string; new: Record<string, unknown> }) => {
          if (!mounted.current) return;
          const change: Change<unknown> = { table: payload.table, eventType: payload.eventType, new: payload.new ?? {} };
          store.current = receive(store.current, change, apply);
          render();
          opts.current.onEvent?.(change, store.current.view);
        }) as never,
      );
    }
    channel.subscribe((status: string) => {
      if (!mounted.current) return;
      setConnection((prev) => nextConnectionState(prev, status));
      scheduler.onStatus(status);
    });
    return () => {
      mounted.current = false;
      scheduler.dispose();
      void client.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindingsKey, refetch]);

  const update = useCallback((fn: (view: V) => V) => {
    store.current = { ...store.current, view: fn(store.current.view) };
    render();
  }, []);

  return {
    view: store.current.view,
    connection,
    error,
    fetching: store.current.fetching,
    refresh: () => void refetch(),
    update,
    current: () => store.current.view,
  };
}
