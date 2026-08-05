# Cyl video queue (#469) — ship a minimal v1, defer the rest?

**The ask, up front:** we want to ship #469 as a **minimal first version** and defer everything
else to a v2. Before building any more, we'd like your read on whether that's the right call — so
we don't spend time on parts that might get reworked or scrapped, or that aren't even needed at our
scale.

**Tracking:** [#604](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/604) (parent)
· [#605](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/605) (retry / requeue).

---

## What v1 ships (the basic flow)

- A request **enqueues** a video job.
- A `cyl-video-worker` container **reads** the job from the queue.
- It **renders** the scan's video.
- The job's **final status** (`complete` / `failed`) is written to the `cyl_video_jobs` table.

That's the whole scope: enqueue → consume → render → record status. Nothing more.

## What v1 defers to v2 (not built)

All of these are tracked under the queue-hardening parent, **#604**:

- **Retrying** a job when the render fails (today a failure is final — you'd re-submit the request).
  → **#605**.
- **Recovering / requeuing** a job when the worker container crashes mid-render. → **#605**
  (lost-job recovery).
- **Knowing whether the worker is actually alive** — recovery today is a blind timeout, not a
  liveness check (see question 3 below). → part of **#605**.
- **Cleaning up** jobs left stuck at `processing` (a stale-job sweep). → tracked in **#604**
  (not yet its own issue).
- **Backpressure** — any cap on how much can be queued at once. → tracked in **#604** (not yet its
  own issue).

## What we need from you

1. **Is shipping the minimal v1 above OK for now?**
2. **Are the deferred items even worth building?** At our expected scale, do retry / crash-recovery /
   caps actually matter, or is v1 enough for a good while?
3. **Is the recovery approach even the right one?** Today crash recovery is a **blind timer** — the
   queue never hears back from the worker, so after the timeout it just *assumes* the worker died
   and hands the job out again. It has no signal for whether the worker is still rendering or
   actually gone. That means a slow-but-alive render can be redelivered and done twice, and a truly
   dead job always waits the full timeout. Is a **heartbeat / progress signal** from the worker
   worth building so recovery is based on real liveness — or is the fixed timeout good enough at our
   scale?

---

## Why we're asking you specifically

There are two separate pgmq queues, for two different jobs — they are not the same feature:

- **Pipeline dispatch queue (#570, merged).** Triggers the A4 sleap-roots pipeline. It **only
  enqueues**; its consumer (a later phase) submits the job to **Argo on the cluster, outside our
  stack** — an external trigger, not a container we run.
- **Cyl video queue (#469, this PR).** A different queue whose consumer **is an in-stack
  `cyl-video-worker`** that renders scan videos inside our Docker stack.

Different queues, different work — but both are drained by the same pgmq claim/complete/fail
pattern, so both eventually face the same error/crash questions. #469 is the first with a live
consumer, so it hits them first. Your #570 design doc already notes its future consumer should
model that claim/complete/fail shape on #469's — so if we do build v2, agreeing on one pattern
keeps the two consistent instead of solving it twice.

---

## Reference: how v1 behaves today

The worker "claims" a job by reading its message, which hides it from other workers for a
**visibility timeout** (a couple of minutes). Three outcomes:

| Outcome                                                     | Queue message        | Job row             | Retried?                                                                              |
| ----------------------------------------------------------- | -------------------- | ------------------- | ------------------------------------------------------------------------------------- |
| **Success**                                           | deleted after render | `complete`        | No — done                                                                            |
| **Caught failure** (render throws, worker catches it) | removed              | `failed`          | **No — final today**                                                           |
| **Worker crash** (killed mid-render, never reports)   | stays on the queue   | stuck`processing` | Redelivered after the timeout; a crash-loop limit (~5 tries) stops it looping forever |

Two things already true and worth keeping: the message is **deleted only after success** (so a crash can't silently drop a job), and a persistently-crashing job is eventually set aside rather
than retried forever.

That's the current state. The open question is whether to build anything beyond it now — which is what we'd like your call on.
