"use client";

import { useState } from "react";
import Link from "next/link";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import styles from "../login/login.module.css";

const LOCAL_PART = /[^A-Za-z0-9._+\-]/g;

function sanitizeLocalPart(raw: string): string {
  const atIdx = raw.indexOf("@");
  const beforeAt = atIdx >= 0 ? raw.slice(0, atIdx) : raw;
  return beforeAt.replace(LOCAL_PART, "");
}

export default function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const supabase = createClientSupabaseClient();

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    if (!email) {
      setError("Please enter your Salk login");
      return;
    }
    setSubmitting(true);
    const fullEmail = email + "@salk.edu";
    const { error } = await supabase.auth.resetPasswordForEmail(fullEmail, {
      redirectTo: `${location.origin}/auth/callback?next=/reset-password`,
    });
    setSubmitting(false);
    if (error) {
      setError(error.message);
    } else {
      setSuccess(
        "If an account exists for that address, a reset link is on its way.",
      );
    }
  };

  return (
    <form className={styles.formCard} onSubmit={handleSubmit} noValidate>
      <h2 className={styles.formTitle}>Reset your password</h2>
      <p className={styles.formHint}>
        We&apos;ll email you a link to set a new password.{" "}
        <Link className={styles.modeToggle} href="/login">
          Back to sign in
        </Link>
      </p>

      <label className={styles.label} htmlFor="forgot-email">
        Salk email
      </label>
      <div className={styles.field}>
        <input
          id="forgot-email"
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

      {error ? (
        <div className={`${styles.banner} ${styles.bannerError}`} role="alert">
          {error}
        </div>
      ) : null}
      {success ? (
        <div
          className={`${styles.banner} ${styles.bannerSuccess}`}
          role="status"
        >
          {success}
        </div>
      ) : null}

      <button type="submit" className={styles.btnSubmit} disabled={submitting}>
        {submitting ? "Sending…" : "Send reset link"}
      </button>
    </form>
  );
}
