import "@/styles/globals.css";
import localFont from "next/font/local";

/**
 * Fraunces — the italic display serif on the login page, shipped with the app (SIL OFL, see
 * app/fonts/OFL.txt). One variable file covers weights 400–600.
 *
 * The variable name --font-fraunces is referenced by
 * web/app/(public)/login/login.module.css for all italic headings.
 */
const fraunces = localFont({
  src: "./fonts/fraunces-italic-latin.woff2",
  style: "italic",
  weight: "400 600",
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
