import { redirect } from "next/navigation";
import { getUser } from "@/lib/supabase/server";
import ForgotPasswordForm from "./ForgotPasswordForm";
import styles from "../login/login.module.css";

export default async function ForgotPasswordPage() {
  const user = await getUser();
  if (user) {
    redirect("/app");
  }

  return (
    <div className={styles.page}>
      <div className={styles.bgWash} aria-hidden />
      <div className={styles.bgGrain} aria-hidden />

      <div className={styles.pageInner}>
        <div className={styles.middle}>
          <section className={styles.hero}>
            <div className={styles.logoRow}>
              <img
                src="/login/logo-mark.png"
                alt="Bloom"
                width={88}
                height={88}
              />
              <span className={styles.wordmark}>Bloom</span>
            </div>
            <p className={styles.kicker}>PASSWORD RESET</p>
            <h1 className={styles.heroTitle}>
              Forgot your <em>password</em>?
            </h1>
            <p className={styles.heroSub}>
              Enter your Salk login below and we&apos;ll send a reset link to
              your inbox.
            </p>
          </section>

          <ForgotPasswordForm />
        </div>
      </div>
    </div>
  );
}
