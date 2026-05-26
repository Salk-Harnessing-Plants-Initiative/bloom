"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import styles from "../login/login.module.css";

const LOCAL_PART = /[^A-Za-z0-9._+\-]/g;
const RESEND_COOLDOWN_SECONDS = 30;

function sanitizeLocalPart(raw: string): string {
  const atIdx = raw.indexOf("@");
  const beforeAt = atIdx >= 0 ? raw.slice(0, atIdx) : raw;
  return beforeAt.replace(LOCAL_PART, "");
}

type Stage = "request" | "verify";

export default function ForgotPasswordForm() {
  const [stage, setStage] = useState<Stage>("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  const router = useRouter();
  const supabase = createClientSupabaseClient();

  useEffect(() => {
    if (resendCooldown <= 0) return;
    const t = setTimeout(() => setResendCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [resendCooldown]);

  const fullEmail = email + "@salk.edu";

  const sendResetEmail = async () => {
    const { error } = await supabase.auth.resetPasswordForEmail(fullEmail, {
      redirectTo: `${location.origin}/auth/callback?next=/reset-password`,
    });
    return error;
  };

  const handleRequest = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setInfo("");
    if (!email) {
      setError("Please enter your Salk login");
      return;
    }
    setSubmitting(true);
    const err = await sendResetEmail();
    setSubmitting(false);
    if (err) {
      setError(err.message);
      return;
    }
    setStage("verify");
    setInfo(`We sent a code to ${fullEmail}.`);
    setResendCooldown(RESEND_COOLDOWN_SECONDS);
  };

  const handleVerify = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError("");
    setInfo("");
    if (!code) {
      setError("Enter the code from your email");
      return;
    }
    setSubmitting(true);
    const { error } = await supabase.auth.verifyOtp({
      email: fullEmail,
      token: code.trim(),
      type: "recovery",
    });
    setSubmitting(false);
    if (error) {
      setError(error.message);
      return;
    }
    router.push("/reset-password");
  };

  const handleResend = async () => {
    if (resendCooldown > 0) return;
    setError("");
    setInfo("");
    const err = await sendResetEmail();
    if (err) {
      setError(err.message);
      return;
    }
    setInfo("Resent. Check your inbox.");
    setResendCooldown(RESEND_COOLDOWN_SECONDS);
  };

  if (stage === "request") {
    return (
      <form className={styles.formCard} onSubmit={handleRequest} noValidate>
        <h2 className={styles.formTitle}>Reset your password</h2>
        <p className={styles.formHint}>
          We&apos;ll email you a link and a 6-digit code.{" "}
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

        <button type="submit" className={styles.btnSubmit} disabled={submitting}>
          {submitting ? "Sending…" : "Send reset email"}
        </button>
      </form>
    );
  }

  return (
    <form className={styles.formCard} onSubmit={handleVerify} noValidate>
      <h2 className={styles.formTitle}>Check your email</h2>
      <p className={styles.formHint}>
        Click the link we sent to <strong>{fullEmail}</strong>, or enter the
        6-digit code below.
      </p>

      <label className={styles.label} htmlFor="forgot-code">
        Verification code
      </label>
      <div className={styles.field}>
        <input
          id="forgot-code"
          name="code"
          inputMode="numeric"
          autoComplete="one-time-code"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="123456"
          maxLength={10}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, ""))}
        />
      </div>

      {error ? (
        <div className={`${styles.banner} ${styles.bannerError}`} role="alert">
          {error}
        </div>
      ) : null}
      {info ? (
        <div className={`${styles.banner} ${styles.bannerSuccess}`} role="status">
          {info}
        </div>
      ) : null}

      <button type="submit" className={styles.btnSubmit} disabled={submitting}>
        {submitting ? "Verifying…" : "Verify code"}
      </button>

      <div className={styles.row}>
        <button
          type="button"
          className={styles.modeToggle}
          onClick={handleResend}
          disabled={resendCooldown > 0}
        >
          {resendCooldown > 0
            ? `Resend in ${resendCooldown}s`
            : "Resend email"}
        </button>
        <button
          type="button"
          className={styles.modeToggle}
          onClick={() => {
            setStage("request");
            setCode("");
            setError("");
            setInfo("");
          }}
        >
          Use a different email
        </button>
      </div>
    </form>
  );
}
