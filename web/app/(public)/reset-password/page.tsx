import { redirect } from "next/navigation";
import { getUser } from "@/lib/supabase/server";
import ResetPasswordForm from "./ResetPasswordForm";
import styles from "../login/login.module.css";

export default async function ResetPasswordPage() {
  const user = await getUser();
  if (!user) {
    redirect("/forgot-password");
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
              Set a <em>new</em> password.
            </h1>
            <p className={styles.heroSub}>
              You&apos;re signed in via the reset link. Pick a new password
              below to finish.
            </p>
          </section>

          <ResetPasswordForm />
        </div>
      </div>
    </div>
  );
}
