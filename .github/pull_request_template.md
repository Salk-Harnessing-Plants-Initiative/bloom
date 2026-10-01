## Summary

<!-- What this PR does, in a sentence or two someone outside the work can follow. -->

## Changes

-

## Testing

<!-- The commands you ran and what they showed. -->

## Schema changes

<!-- Delete this section if this PR changes no migrations. -->

<!-- Paste the output of `make erd-snapshot CHANGED=origin/staging` here: a mermaid erDiagram of the tables this PR creates or changes, and their neighbours. -->

| Name | Table | Type | What it refuses | How it is added |
| ---- | ----- | ---- | --------------- | --------------- |

<!--
One row per constraint or index the migrations add. "How it is added" is one of:
drop, then add (CHECK); guarded add (FOREIGN KEY); guarded add that compares the definition
(UNIQUE, PRIMARY KEY, EXCLUDE); inline in CREATE TABLE IF NOT EXISTS; IF NOT EXISTS (index).
Add NOT VALID where existing rows may violate it. Check the section with
`make pr-body-check BODY=<file>`.

If the migrations change no table, constraint or index, delete the diagram and table above
and keep only this line:
No schema changes.
-->

## Related issues

<!-- A closing keyword before each issue number, one keyword per issue. -->
