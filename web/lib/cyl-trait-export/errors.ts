/**
 * The one error type an export raises. `detail` is always a fixed message safe to
 * show a user (design D7): it never carries PostgREST's message, details or hint.
 */

export type ExportErrorKind =
  | 'rpc'
  | 'integrity'
  | 'selection_changed'
  | 'not_in_selection'
  | 'deadline'
  | 'cancelled'

export class ExportError extends Error {
  readonly kind: ExportErrorKind
  readonly detail: string

  constructor(kind: ExportErrorKind, detail: string) {
    super(detail)
    this.name = 'ExportError'
    this.kind = kind
    this.detail = detail
  }
}

export const SELECTION_CHANGED = 'the selection changed during the export; retry'
