## ADDED Requirements

### Requirement: Declared Errors May Carry Their Own Remedy

`BloomMCPError.from_exception` SHALL, when mapping a **declared** exception
(one the tool author opted into via `errors=`), use the exception's
`agent_remedy` attribute as the resulting `tool_error`'s structured `remedy`
when the exception (class or instance) defines one non-empty, falling back to
the existing stock advice ("Check the inputs/experiment for this tool and
retry.") otherwise. The override exists because `remedy` is the field
designed to drive agent behaviour, and the stock advice pairs a retry
instruction with messages that can describe permanent conditions — the #573
foreign-catalog errors (`CatalogBackendMismatchError`,
`ForeignCatalogError`) are the first shipped types whose whole contract is
"do not retry", and each SHALL define an `agent_remedy` that directs
investigation (via the storage documentation) and does not invite a retry.
The override applies only to declared exceptions: undeclared failures keep
the fixed `internal_error` message + remedy, so the no-internals-leak
promise is unchanged.

#### Scenario: A declared error's own remedy replaces the stock retry advice

- **WHEN** a declared exception carrying a non-empty `agent_remedy` is mapped
  by `BloomMCPError.from_exception`
- **THEN** the structured error's `code` is `tool_error`, its `message` is
  the exception's own text, and its `remedy` equals the exception's
  `agent_remedy` — for the #573 types, a remedy that names the storage
  documentation and contains no retry instruction

#### Scenario: Declared errors without a remedy keep the default

- **WHEN** a declared exception with no `agent_remedy` attribute (or an empty
  one) is mapped
- **THEN** the structured error carries the stock "Check the
  inputs/experiment for this tool and retry." remedy, exactly as before this
  change

#### Scenario: Undeclared failures are unaffected

- **WHEN** an undeclared exception is mapped — even one that happens to carry
  an `agent_remedy` attribute
- **THEN** it surfaces as `internal_error` with the fixed message,
  correlation id, and fixed remedy, never the exception's own text or remedy
