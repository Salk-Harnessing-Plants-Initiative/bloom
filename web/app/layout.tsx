import "@/styles/globals.css";
import { Fraunces } from "next/font/google";

/**
 * Fraunces — the display serif used on the redesigned login page and
 * any future marketing/public surfaces. Self-hosted via next/font so
 * there's no FOUC and no runtime DNS/TLS to fonts.googleapis.com.
 *
 * The variable name --font-fraunces is referenced by
 * web/app/(public)/login/login.module.css for all italic headings.
 */
const fraunces = Fraunces({
  subsets: ["latin"],
  style: ["italic"],
  weight: ["400", "500", "600"],
  display: "swap",
  variable: "--font-fraunces",
});

export const metadata = {
  title: "Bloom",
  description: "Web app for Salk Harnessing Plants Initiative",
  icons: {
    icon: "/login/favicon.png",
  },
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={fraunces.variable}>
      <body>{children}</body>
    </html>
  );
}
