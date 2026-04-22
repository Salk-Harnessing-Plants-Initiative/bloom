import { redirect } from "next/navigation";
import { getUser } from "@/lib/supabase/server";
import LoginForm from "./LoginForm";
import styles from "./login.module.css";

const PLANTS: Array<{
  src: string;
  cls: string;
  drift?: 1 | 2 | 3;
  name: string;
  traits: number;
}> = [
  { src: "/login/wheat-watercolor.png", cls: "p1", drift: 1, name: "Wheat", traits: 47 },
  { src: "/login/amaranth-watercolor.png", cls: "p2", drift: 2, name: "Amaranth", traits: 32 },
  { src: "/login/spinach-watercolor.png", cls: "p4", drift: 1, name: "Spinach", traits: 28 },
  { src: "/login/alfalfa-watercolor.svg", cls: "p5", drift: 2, name: "Alfalfa", traits: 18 },
  { src: "/login/rice-watercolor.svg", cls: "p6", drift: 3, name: "Rice", traits: 52 },
  { src: "/login/pennycress-watercolor.svg", cls: "p7", drift: 1, name: "Pennycress", traits: 25 },
  { src: "/login/soybean-watercolor.svg", cls: "p8", drift: 3, name: "Soybean", traits: 15 },
  { src: "/login/sorghum-watercolor.svg", cls: "p9", drift: 1, name: "Sorghum", traits: 12 },
  { src: "/login/tomato-watercolor.png", cls: "p10", drift: 2, name: "Tomato", traits: 53 },
  { src: "/login/canola-watercolor.svg", cls: "p11", drift: 3, name: "Canola", traits: 7 },
];

export default async function LoginPage() {
  const user = await getUser();
  if (user) {
    redirect("/app");
  }

  return (
    <div className={styles.page}>
      <div className={styles.bgWash} aria-hidden />
      <div className={styles.bgGrain} aria-hidden />

      {PLANTS.map((p, i) => (
        <div
          key={i}
          className={`${styles.plant} ${styles[p.cls]}${
            p.drift ? ` ${styles[`drift${p.drift}`]}` : ""
          }`}
        >
          <img src={p.src} alt={p.name} />
          <div className={styles.callout} role="tooltip">
            <strong>{p.name}</strong> · {p.traits} traits measured
          </div>
        </div>
      ))}

      <div className={styles.pageInner}>
        <div className={styles.topRow}>
          <a
            className={styles.hpiLink}
            href="https://www.salk.edu/harnessing-plants-initiative/"
            target="_blank"
            rel="noopener"
          >
            SALK HPI ↗
          </a>
        </div>

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
            <p className={styles.kicker}>THE DATA PLATFORM</p>
            <h1 className={styles.heroTitle}>
              For the plants <em>solving</em> climate change.
            </h1>
            <p className={styles.heroSub}>
              Phenotyping scans, single-cell expression, gene candidates, and
              eight years of field work from the Salk Harnessing Plants
              Initiative.
            </p>
          </section>

          <LoginForm />
        </div>

        <div className={styles.statsStrip}>
          <div className={styles.stat}>
            <div className={styles.statNum}>14</div>
            <div className={styles.statMeta}>
              <div className={styles.statLabel}>SPECIES</div>
              <div className={styles.statSub}>
                Engineered for deeper roots, more suberin
              </div>
            </div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statNum}>47</div>
            <div className={styles.statMeta}>
              <div className={styles.statLabel}>EXPERIMENTS</div>
              <div className={styles.statSub}>Across 8 years of waves</div>
            </div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statNum}>450</div>
            <div className={styles.statMeta}>
              <div className={styles.statLabel}>GENE CANDIDATES</div>
              <div className={styles.statSub}>Orthologs, progress, notes</div>
            </div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statNum}>11</div>
            <div className={styles.statMeta}>
              <div className={styles.statLabel}>EXPRESSION ATLASES</div>
              <div className={styles.statSub}>
                Single-cell RNA · UMAPs, DE
              </div>
            </div>
          </div>
        </div>

        <div className={styles.bottomRow}>
          <span>© 2026 Salk Institute · HPI</span>
          <div className={styles.footLinks}>
            <a
              href="https://www.salk.edu/harnessing-plants-initiative/"
              target="_blank"
              rel="noopener"
            >
              About HPI
            </a>
            <a href="mailto:dbutler@salk.edu?subject=Bloom%20support">Support</a>
          </div>
        </div>
      </div>
    </div>
  );
}
