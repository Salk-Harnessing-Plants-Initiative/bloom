# _WIKI

Component reference docs for the bloom monorepo — a Next.js + Supabase

For getting the stack running, see the root [README.md](../README.md),
[DEV_SETUP.md](../DEV_SETUP.md), or [PROD_SETUP.md](../PROD_SETUP.md).
The docs below describe how each component is wired *after* it's up.

## Layout

Each component is a subfolder. The folder's `README.md` is the entry
point.

```
_WIKI/
├── README.md            (this file)
├── SUPABASE/
│   ├── README.md        Self-hosted Supabase stack: roles, RLS, storage,
│   │                    JWT flow, migration conventions, known issues.
│   ├── erd.md           Generated ER diagram (make erd).
│   ├── trait-recipes.md Trait recipes, recipe-aware reads, export sidecar v1.
│   ├── trait-recipes.export.schema.json   Export sidecar v1 JSON Schema.
│   └── trait-recipes.export.example.json  An illustrative sidecar.
├── BLOOMMCP/
│   └── README.md        bloommcp MCP server: architecture, tool surface,
│                        auth, Supabase integration, dev gotchas.
└── ARGO/
    └── README.md        Argo workflows on Run:ai: how an RNA-seq run starts,
                         where its result goes, starting one by hand.
```
