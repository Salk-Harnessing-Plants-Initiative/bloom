<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# UI conventions

- **Info icons (ⓘ):** use `components/info-tip.tsx` for every one, never a hand-styled "i". It is Bloom's green: a dark-green "i" (`text-lime-800`) in a green circle (`border-lime-700`) on light green (`bg-lime-50`), and its note opens on hover or keyboard focus in a light-green box. Give it a `label` for screen readers and put the note in `children`; use `align="right"` near the right edge. It's a client component, so a server component can render it too. The note stays open while the pointer is on it, closes on Esc, and is the icon's `aria-describedby`. Don't put it inside a `<button>`: it is focusable itself, and a focusable element inside a button is invalid HTML.
- **Live views:** a list kept up to date through Realtime (`lib/cyl-pipeline/use-live-sync.ts`) shows `LiveIndicator` with the channel's state (`live.connection`, `live.refresh`), so people can tell when it stops updating.
