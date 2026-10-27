/**
 * A recording Supabase browser-client double for the pipeline-run views' tests
 * (add-cyl-pipeline-ui task 3.0), modelled on
 * components/expression-differential-analysis.test.tsx.
 *
 * - `from(table)` returns a thenable query builder that records every call in
 *   order and answers through the per-test `respond` function.
 * - `channel(topic)` returns a channel whose `.on()` returns itself and whose
 *   `.subscribe(cb)` captures `cb`. Tests drive it with `status()` and `emit()`.
 *   `emit()` deliberately ignores the binding's `filter`: the server applies
 *   filters, so a view must still drop events that aren't its own.
 * - `removeChannel` is a spy.
 * - `auth.getSession()` answers `supabaseMock.session`, and `realtime.setAuth`
 *   is a spy; `supabaseMock.log` records setAuth and subscribe calls in order.
 *
 * Use it with
 *   vi.mock("@/lib/supabase/client", async () =>
 *     (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
 * and call `resetSupabaseMock()` in `beforeEach`.
 */

import { vi, type Mock } from "vitest";

export interface Answer {
  data: unknown;
  error: { message: string; code?: string } | null;
}

export interface RecordedCall {
  method: string;
  args: unknown[];
}

export interface RecordedQuery {
  table: string;
  calls: RecordedCall[];
  /** The arguments of the first call to `method`, or undefined. */
  arg(method: string): unknown[] | undefined;
  /** The arguments of every call to `method`, in order. */
  all(method: string): unknown[][];
}

export type Responder = (query: RecordedQuery) => Answer | Promise<Answer>;

const BUILDER_METHODS = [
  "select",
  "eq",
  "in",
  "or",
  "not",
  "is",
  "lt",
  "gte",
  "order",
  "limit",
  "range",
  "abortSignal",
] as const;

export interface Binding {
  type: string;
  filter: Record<string, string>;
  callback: (payload: unknown) => void;
}

export interface MockChannel {
  topic: string;
  bindings: Binding[];
  subscribed: boolean;
  statusCallback: ((status: string, err?: Error) => void) | null;
  on(type: string, filter: Record<string, string>, callback: (payload: unknown) => void): MockChannel;
  subscribe(callback?: (status: string, err?: Error) => void): MockChannel;
  /** Report a channel status, as realtime-js does. */
  status(status: string): void;
  /** Deliver a postgres_changes payload to every binding for its table and event. */
  emit(payload: { table: string; eventType: string; new?: unknown; old?: unknown }): void;
}

interface MockState {
  queries: RecordedQuery[];
  channels: MockChannel[];
  respond: Responder;
  removeChannel: Mock<(channel: MockChannel) => Promise<string>>;
  /** The session auth.getSession() answers; null for signed out. */
  session: { access_token: string; user?: { id: string } } | null;
  setAuth: Mock<(token?: string | null) => Promise<void>>;
  /** Ordered record of setAuth and subscribe calls, to check which came first. */
  log: string[];
}

const EMPTY: Answer = { data: [], error: null };

export const supabaseMock: MockState = {
  queries: [],
  channels: [],
  respond: () => EMPTY,
  removeChannel: vi.fn(async (_channel: MockChannel) => "ok"),
  session: { access_token: "user-token" },
  setAuth: vi.fn(async () => {}),
  log: [],
};

/** Clear every recorded query and channel, and answer `[]` until told otherwise. */
export function resetSupabaseMock(respond: Responder = () => EMPTY): void {
  supabaseMock.queries = [];
  supabaseMock.channels = [];
  supabaseMock.respond = respond;
  supabaseMock.removeChannel = vi.fn(async (channel: MockChannel) => {
    channel.subscribed = false;
    return "ok";
  });
  supabaseMock.session = { access_token: "user-token" };
  supabaseMock.log = [];
  supabaseMock.setAuth = vi.fn(async (token?: string | null) => {
    supabaseMock.log.push(`setAuth:${token}`);
  });
}

/** Queries recorded against one table. */
export function queriesFor(table: string): RecordedQuery[] {
  return supabaseMock.queries.filter((q) => q.table === table);
}

/** Channels not yet removed. */
export function liveChannels(): MockChannel[] {
  const removed = new Set(supabaseMock.removeChannel.mock.calls.map((c) => c[0]));
  return supabaseMock.channels.filter((c) => !removed.has(c));
}

function recordedQuery(table: string): RecordedQuery {
  const calls: RecordedCall[] = [];
  return {
    table,
    calls,
    arg: (method) => calls.find((c) => c.method === method)?.args,
    all: (method) => calls.filter((c) => c.method === method).map((c) => c.args),
  };
}

function queryBuilder(table: string) {
  const recorded = recordedQuery(table);
  let sent = false;
  const send = (): Promise<Answer> => {
    if (!sent) {
      sent = true;
      supabaseMock.queries.push(recorded);
    }
    return Promise.resolve().then(() => supabaseMock.respond(recorded));
  };
  const builder: Record<string, unknown> = {};
  for (const method of BUILDER_METHODS) {
    builder[method] = (...args: unknown[]) => {
      recorded.calls.push({ method, args });
      return builder;
    };
  }
  builder.maybeSingle = () => {
    recorded.calls.push({ method: "maybeSingle", args: [] });
    return send();
  };
  builder.then = (resolve: (v: Answer) => unknown, reject?: (e: unknown) => unknown) =>
    send().then(resolve, reject);
  return builder;
}

function mockChannel(topic: string): MockChannel {
  const early: string[] = [];
  const channel: MockChannel = {
    topic,
    bindings: [],
    subscribed: false,
    statusCallback: null,
    on(type, filter, callback) {
      channel.bindings.push({ type, filter, callback });
      return channel;
    },
    subscribe(callback) {
      supabaseMock.log.push(`subscribe:${topic}`);
      channel.subscribed = true;
      channel.statusCallback = callback ?? null;
      for (const status of early.splice(0)) channel.status(status);
      return channel;
    },
    status(status) {
      // A view subscribes only after reading the session, so a test can report
      // a status first; hold it until subscribe, as the socket would.
      if (!channel.statusCallback) {
        early.push(status);
        return;
      }
      channel.statusCallback(status, status === "SUBSCRIBED" ? undefined : new Error(status));
    },
    emit(payload) {
      for (const b of channel.bindings) {
        if (b.type !== "postgres_changes" || b.filter.table !== payload.table) continue;
        if (b.filter.event !== "*" && b.filter.event !== payload.eventType) continue;
        b.callback({ schema: "public", commit_timestamp: "", errors: null, old: {}, new: {}, ...payload });
      }
    },
  };
  return channel;
}

export const mockClient = {
  from: (table: string) => queryBuilder(table),
  channel: (topic: string) => {
    const channel = mockChannel(topic);
    supabaseMock.channels.push(channel);
    return channel;
  },
  removeChannel: (channel: MockChannel) => supabaseMock.removeChannel(channel),
  auth: {
    getSession: async () => ({ data: { session: supabaseMock.session }, error: null }),
  },
  realtime: {
    setAuth: (token?: string | null) => supabaseMock.setAuth(token),
  },
};

/** The module shape of `@/lib/supabase/client`, for `vi.mock`. */
export const clientModule = {
  createClientSupabaseClient: () => mockClient,
};

export interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

/** A promise with its settle functions (not `Promise.withResolvers`: CI runs Node 20). */
export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
