"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import styles from "./login.module.css";

const LOCAL_PART = /[^A-Za-z0-9._+\-]/g;

function sanitizeLocalPart(raw: string): string {
  const atIdx = raw.indexOf("@");
  const beforeAt = atIdx >= 0 ? raw.slice(0, atIdx) : raw;
  return beforeAt.replace(LOCAL_PART, "");
}

export default function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [view, setView] = useState<"sign-in" | "sign-up">("sign-in");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [remember, setRemember] = useState(true);
  const router = useRouter();
  const supabase = createClientSupabaseClient();

  const handleSignUp = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!email) {
      setError("Please fill in the email field");
      return;
    }
    if (!password) {
      setError("Please fill in the password field");
      return;
    }
    const { data, error } = await supabase.auth.signUp({
      email: email + "@salk.edu",
      password,
      options: {
        emailRedirectTo: `${location.origin}/auth/callback`,
        data: { is_admin: false },
      },
    });
    if (error) {
      setError(error.message);
    } else if (data?.user) {
      setSuccess("Account created successfully! Signing you in...");
      setError("");
      const { error: signInError } = await supabase.auth.signInWithPassword({
        email: email + "@salk.edu",
        password,
      });
      if (signInError) {
        setError(signInError.message);
        setSuccess("");
      } else {
        router.push("/app");
      }
    }
  };

  const handleSignIn = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!email) {
      setError("Please fill in the email field");
      return;
    }
    if (!password) {
      setError("Please fill in the password field");
      return;
    }
    const { error } = await supabase.auth.signInWithPassword({
      email: email + "@salk.edu",
      password,
    });
    if (error) {
      setError(error.message);
    } else {
      router.push("/app");
    }
  };

  const toggleView = () => {
    setView((v) => (v === "sign-in" ? "sign-up" : "sign-in"));
    setError("");
    setSuccess("");
  };

  const isSignUp = view === "sign-up";

  return (
    <form
      className={styles.formCard}
      onSubmit={isSignUp ? handleSignUp : handleSignIn}
      noValidate
    >
      <h2 className={styles.formTitle}>
        {isSignUp ? "Create your account" : "Sign in to Bloom"}
      </h2>
      <p className={styles.formHint}>
        {isSignUp ? "Already have an account? " : "No account yet? "}
        <button
          type="button"
          className={styles.modeToggle}
          onClick={toggleView}
        >
          {isSignUp ? "Sign in" : "Sign up"}
        </button>
      </p>

      <label className={styles.label} htmlFor="login-email">
        Salk email
      </label>
      <div className={styles.field}>
        <input
          id="login-email"
          name="email"
          inputMode="email"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="your-login"
          value={email}
          onChange={(e) => setEmail(sanitizeLocalPart(e.target.value))}
          onPaste={(e) => {
            e.preventDefault();
            const text = e.clipboardData.getData("text");
            setEmail(sanitizeLocalPart(text));
          }}
        />
        <span className={styles.fieldSuffix}>@salk.edu</span>
      </div>

      <label className={styles.label} htmlFor="login-password">
        Password
      </label>
      <div className={styles.field}>
        <input
          id="login-password"
          name="password"
          type="password"
          autoComplete={isSignUp ? "new-password" : "current-password"}
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>

      <div className={styles.row}>
        <label className={styles.checkbox}>
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />
          Keep me signed in
        </label>
        {!isSignUp && (
          <a className={styles.forgot} href="mailto:dbutler@salk.edu?subject=Bloom%20password%20reset">
            Forgot password?
          </a>
        )}
      </div>

      {error ? (
        <div className={`${styles.banner} ${styles.bannerError}`} role="alert">
          {error}
        </div>
      ) : null}
      {success ? (
        <div className={`${styles.banner} ${styles.bannerSuccess}`} role="status">
          {success}
        </div>
      ) : null}

      <button type="submit" className={styles.btnSubmit}>
        {isSignUp ? "Create account" : "Sign in"}
      </button>
    </form>
  );
}
