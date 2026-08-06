'use client'

import { useState } from 'react'
import styles from './consent.module.css'

/**
 * Approve/deny controls. The decision is submitted through our own route
 * handler so the user's access token stays server-side.
 */
export default function ConsentForm({
  authorizationId,
  clientName,
}: {
  authorizationId: string
  clientName: string
}) {
  const [pending, setPending] = useState<'approve' | 'deny' | null>(null)
  const [error, setError] = useState('')

  const decide = async (action: 'approve' | 'deny') => {
    setPending(action)
    setError('')
    try {
      const res = await fetch('/api/oauth/consent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ authorization_id: authorizationId, action }),
      })
      const body = await res.json().catch(() => null)
      if (!res.ok || !body?.redirect_url) {
        setError(body?.error || 'Could not complete the request. Please try again.')
        setPending(null)
        return
      }
      window.location.href = body.redirect_url
    } catch {
      setError('Could not reach Bloom. Check your connection and try again.')
      setPending(null)
    }
  }

  return (
    <div>
      {error ? (
        <div className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.btnSecondary}
          disabled={pending !== null}
          onClick={() => decide('deny')}
        >
          {pending === 'deny' ? 'Cancelling…' : 'Cancel'}
        </button>
        <button
          type="button"
          className={styles.btnPrimary}
          disabled={pending !== null}
          onClick={() => decide('approve')}
        >
          {pending === 'approve' ? 'Authorizing…' : `Authorize ${clientName}`}
        </button>
      </div>
    </div>
  )
}
