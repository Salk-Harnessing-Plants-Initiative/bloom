'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'

export default function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [view, setView] = useState('sign-in')
  const [error, setError] = useState('')
  const router = useRouter()
  const supabase = createClientComponentClient()

  const handleSignUp = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!email) {
      setError('Please fill in the email field')
      return
    }
    if (!password) {
      setError('Please fill in the password field')
      return
    }
    const { data, error } = await supabase.auth.signUp({
      email: email + '@salk.edu',
      password,
      options: {
        emailRedirectTo: `${location.origin}/auth/callback`,
      },
    })
    if (error) {
      setError(error.message)
    }
    else {
      setView('check-email')
    }
  }

  const handleSignIn = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const { data, error } = await supabase.auth.signInWithPassword({
      email: email + '@salk.edu',
      password,
    })

    // if (error) {
    //   setError(error.message)
    // }
    // else {
    //   router.push('/app')
    // }
    if (error) {
    setError(error.message);
    } else {
      await fetch("/api/auth/set-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          access_token: data.session?.access_token,
          refresh_token: data.session?.refresh_token,
        }),
      });
      router.push("/app");
    }
  }

  return (
    <div className="flex-1 flex flex-col mx-auto w-full max-w-sm justify-center gap-2">
      {view === 'check-email' ? (
        <p className="text-center text-neutral-400">
          Click the confirmation link sent to <span className="font-bold">{email + '@salk.edu'}</span> to
          continue signing up
        </p>
      ) : (
        <form
          className="flex-1 flex flex-col w-full max-w-sm justify-center gap-2"
          onSubmit={view === 'sign-in' ? handleSignIn : handleSignUp}
        >
          <label className="text-md text-neutral-400" htmlFor="email">
            Email
          </label>
          <div className='flex flex-row'>
            <input
              className="flex-1 rounded-md px-4 py-2 bg-inherit border mb-6"
              name="email"
              onChange={(e) => {
                if (!e.target.value.includes('@')) {
                  setEmail(e.target.value)
                }
              }}
              value={email}
              placeholder="your-login"
            />
            <span className="flex-none rounded-md pl-4 py-2 bg-inherit mb-6">@salk.edu</span>
          </div>

          <label className="text-md text-neutral-400" htmlFor="password">
            Password
          </label>
          <input
            className="rounded-md px-4 py-2 bg-inherit border mb-6"
            type="password"
            name="password"
            onChange={(e) => setPassword(e.target.value)}
            value={password}
            placeholder="password"
          />
          {view === 'sign-in' ? (
            <>
              <button className="bg-green-700 rounded px-4 py-2 text-neutral-200 mb-6">
                Sign In
              </button>
              <p className="text-sm text-neutral-500 text-center">
                Don't have an account?
                <button
                  className="ml-1 underline"
                  onClick={() => setView('sign-up')}
                >
                  Sign Up Now
                </button>
              </p>
            </>
          ) : null}
          {view === 'sign-up' ? (
            <>
              <button className="bg-green-700 rounded px-4 py-2 text-neutral-200 mb-6">
                Sign Up
              </button>
              <p className="text-sm text-neutral-500 text-center">
                Already have an account?
                <button
                  className="ml-1 underline"
                  onClick={() => setView('sign-in')}
                >
                  Sign In Now
                </button>
              </p>
            </>
          ) : null}
          {error ? (
            <p className="text-sm text-red-700 text-center">
              {error}
            </p>
          ) : null}
        </form>
      )}
    </div>
  )
}
