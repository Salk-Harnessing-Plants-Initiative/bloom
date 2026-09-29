"use client";

/**
 * The live views' shared sync loop (spec: "Live views synchronise from
 * Realtime without polling").
 *
 * - It joins only after Realtime has read the session token (see the effect).
 * - One channel per mounted instance, on a unique topic: StrictMode
 *   double-mounts on the singleton browser client, and a reused topic would
 *   hand the second mount the first mount's channel.
 * - Every SUBSCRIBED goes through the resync scheduler (leading plus
 *   trailing). Nothing else here fetches.
 * - Events that arrive during a snapshot fetch are buffered and replayed on
 *   top of it, and so are update() calls. Only the latest fetch's result is
 *   applied. Callers keep any state a snapshot reads inside the view, so a
 *   superseded fetch can't leave it behind.
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
  /** Refetch now (a user action); it opens a resync window like any refetch. */
  refresh: () => void;
  /** Whether a snapshot fetch is in flight, read from the store rather than the last render. */
  isFetching: () => boolean;
  /**
   * Change the held view outside a snapshot. During a fetch the change is
   * buffered and replayed on top of the snapshot, like an event.
   */
  update: (fn: (view: V) => V) => void;
  /** How many snapshots have started; a caller compares it to spot a newer one. */
  generation: () => number;
  /** The current view, for event handlers. */
  current: () => V;
}

let topicSeq = 0;

/** A buffered event, or a buffered update(). */
type Entry<V> = { change: Change<unknown> } | { fn: (view: V) => V };

export function useLiveSync<V>(options: LiveSyncOptions<V>): LiveSync<V> {
  const opts = useRef(options);
  opts.current = options;

  const store = useRef<Synced<V, Entry<V>>>(synced(options.initial));
  const [, render] = useReducer((n: number) => n + 1, 0);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const scheduler = useRef<ReturnType<typeof createResyncScheduler> | null>(null);
  const mounted = useRef(true);

  const apply = (view: V, entry: Entry<V>) => ("fn" in entry ? entry.fn(view) : opts.current.apply(view, entry.change));

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
    const resync = createResyncScheduler(() => void refetch());
    scheduler.current = resync;
    let channel = client.channel(`${opts.current.topic}:${++topicSeq}`);
    for (const b of JSON.parse(bindingsKey) as LiveBinding[]) {
      channel = channel.on(
        // realtime-js overloads `on` per event type; this is the postgres_changes form.
        "postgres_changes" as never,
        { event: b.event, schema: "public", table: b.table, ...(b.filter ? { filter: b.filter } : {}) } as never,
        ((payload: { table: string; eventType: string; new: Record<string, unknown> }) => {
          if (!mounted.current) return;
          const change: Change<unknown> = { table: payload.table, eventType: payload.eventType, new: payload.new ?? {} };
          store.current = receive(store.current, { change }, apply);
          render();
          opts.current.onEvent?.(change, store.current.view);
        }) as never,
      );
    }
    // Join as the signed-in user. On a full page load the browser client has
    // not put the session token on its socket yet, so a join sent now would
    // carry only the anon key: RLS would run as anon and every run event would
    // be dropped without an error (found in add-cyl-pipeline-ui task 8.6).
    // setAuth() with no argument reads the current token through
    // supabase-js's accessToken callback, which awaits auth initialisation
    // (the anon key when signed out). Later tokens arrive the way they do for
    // every channel: supabase-js calls realtime.setAuth on TOKEN_REFRESHED.
    // If the session can't be read, the join is anon and RLS drops events
    // without an error; the next token refresh repairs it.
    let cancelled = false;
    void (async () => {
      try {
        await client.realtime.setAuth();
      } catch (e) {
        // Join anyway; the view shows whatever the channel then reports.
        console.warn("Realtime auth failed; joining without the session token", e);
      }
      if (cancelled) return;
      channel.subscribe((status: string) => {
        if (!mounted.current) return;
        setConnection((prev) => nextConnectionState(prev, status));
        resync.onStatus(status);
      });
    })();
    return () => {
      cancelled = true;
      mounted.current = false;
      resync.dispose();
      if (scheduler.current === resync) scheduler.current = null;
      void client.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindingsKey, refetch]);

  const update = useCallback((fn: (view: V) => V) => {
    store.current = receive(store.current, { fn }, apply);
    render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    view: store.current.view,
    connection,
    error,
    fetching: store.current.fetching,
    refresh: () => (scheduler.current ? scheduler.current.refetchNow() : void refetch()),
    isFetching: () => store.current.fetching,
    update,
    generation: () => generation.current,
    current: () => store.current.view,
  };
}
