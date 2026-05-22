"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import styles from "../login/login.module.css";

const MIN_PASSWORD_LENGTH = 8;

export default function ResetPasswordForm() {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const router = useRouter();
  const supabase = createClientSupabaseClient();

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`);
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    setSubmitting(true);
    const { error } = await supabase.auth.updateUser({ password });
    setSubmitting(false);
    if (error) {
      setError(error.message);
      return;
    }
    setSuccess("Password updated. Redirecting…");
    setTimeout(() => router.push("/app"), 800);
  };

  return (
    <form className={styles.formCard} onSubmit={handleSubmit} noValidate>
      <h2 className={styles.formTitle}>Choose a new password</h2>
      <p className={styles.formHint}>
        Pick something at least {MIN_PASSWORD_LENGTH} characters long.
      </p>

      <label className={styles.label} htmlFor="reset-password">
        New password
      </label>
      <div className={styles.field}>
        <input
          id="reset-password"
          name="password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>

      <label className={styles.label} htmlFor="reset-confirm">
        Confirm new password
      </label>
      <div className={styles.field}>
        <input
          id="reset-confirm"
          name="confirm"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
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
        {submitting ? "Updating…" : "Update password"}
      </button>
    </form>
  );
}
