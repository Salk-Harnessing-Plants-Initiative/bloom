/**
 * OAuth 2.1 authorization screen.
 *
 * Supabase Auth redirects here mid-flow (the route named by
 * `GOTRUE_OAUTH_SERVER_AUTHORIZATION_PATH`) so the user can approve or refuse
 * an application's request. Nothing is granted by loading this page — consent
 * is only recorded when the user acts on it.
 */

import { redirect } from 'next/navigation'
import { getSession, getUser } from '@/lib/supabase/server'
import { fetchAuthorization, describeScopes } from '@/lib/oauth-consent'
import ConsentForm from './ConsentForm'
import styles from './consent.module.css'

export const dynamic = 'force-dynamic'

export default async function ConsentPage({
  searchParams,
}: {
  searchParams: Promise<{ authorization_id?: string }>
}) {
  const { authorization_id: authorizationId } = await searchParams

  if (!authorizationId) {
    return (
      <Problem
        title="Missing authorization request"
        detail="This page can only be opened as part of an application's sign-in request."
      />
    )
  }

  const user = await getUser()
  if (!user) {
    const next = `/oauth/consent?authorization_id=${encodeURIComponent(authorizationId)}`
    redirect(`/login?next=${encodeURIComponent(next)}`)
  }

  const session = await getSession()
  const accessToken = session?.access_token
  if (!accessToken) {
    return (
      <Problem
        title="Your session has expired"
        detail="Sign in again, then retry the request from the application."
      />
    )
  }

  const authorization = await fetchAuthorization(authorizationId, accessToken)
  if (!authorization) {
    return (
      <Problem
        title="This request is no longer valid"
        detail="It may have already been used or timed out. Start the sign-in again from the application."
      />
    )
  }

  return (
    <main className={styles.page}>
      <div className={styles.card}>
        <h1 className={styles.title}>Authorize {authorization.client.name}</h1>
        <p className={styles.lede}>
          <strong>{authorization.client.name}</strong> wants to access Bloom as{' '}
          <strong>{authorization.user.email}</strong>.
        </p>

        <h2 className={styles.sectionTitle}>It will be able to</h2>
        <ul className={styles.scopeList}>
          {describeScopes(authorization.scope).map((s) => (
            <li key={s}>{s}</li>
          ))}
          <li>Run Bloom analysis tools on your behalf</li>
        </ul>

        <p className={styles.note}>
          Approving lets this application reach every Bloom analysis tool — access cannot currently
          be limited to a subset. It reads and writes data through Bloom&apos;s shared service
          account, so it does not gain your personal database access. You can revoke this at any
          time.
        </p>

        <ConsentForm
          authorizationId={authorization.authorization_id}
          clientName={authorization.client.name}
        />
      </div>
    </main>
  )
}

function Problem({ title, detail }: { title: string; detail: string }) {
  return (
    <main className={styles.page}>
      <div className={styles.card}>
        <h1 className={styles.title}>{title}</h1>
        <p className={styles.lede}>{detail}</p>
      </div>
    </main>
  )
}
