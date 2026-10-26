/**
 * The email sent when someone adds a new species (from the Species page or the scRNA job
 * form), so its image can be added: until then it shows the placeholder.
 *
 * It goes to SPECIES_NOTIFY_EMAIL (in .env.*.defaults), through Salk's mail relay: the SMTP_*
 * settings GoTrue's sign-in emails and the run-finished emails use. Without SMTP_HOST or a
 * recipient (dev) nothing is sent. Sending never changes the species, and a failure is only
 * logged.
 */

import nodemailer from "nodemailer";

/** How long to wait for the relay before giving up (it's on Salk's network). */
export const SMTP_TIMEOUT_MS = 15_000;

export interface SpeciesAddedEmail {
  to: string[];
  subject: string;
  text: string;
}

/** The message: which species, who added it, and where to add its image. */
export function buildSpeciesAddedEmail({
  label,
  addedBy,
  siteUrl,
  to,
}: {
  /** "Pennycress (Thlaspi arvense)" */
  label: string;
  /** The email of whoever added it, or null if unknown. */
  addedBy: string | null;
  siteUrl: string;
  to: string[];
}): SpeciesAddedEmail {
  const page = `${siteUrl.replace(/\/+$/, "")}/app/species`;
  return {
    to,
    subject: `New species in Bloom: ${label}`,
    text: [
      `${label} was added to Bloom${addedBy ? ` by ${addedBy}` : ""}.`,
      "",
      "It shows a placeholder image until it has one of its own. To add one, upload its",
      "illustration to the species-illustrations bucket and set the species' illustration_path,",
      "or add an icon to web/public/species-icons.",
      "",
      `Species page: ${page}`,
    ].join("\n"),
  };
}

/** Who gets the email: SPECIES_NOTIFY_EMAIL, which may list several addresses separated by commas. */
export function recipients(env: NodeJS.ProcessEnv): string[] {
  return (env.SPECIES_NOTIFY_EMAIL ?? "")
    .split(",")
    .map((a) => a.trim())
    .filter((a) => a !== "");
}

export interface NotifyDeps {
  env?: NodeJS.ProcessEnv;
  send?: (message: SpeciesAddedEmail, env: NodeJS.ProcessEnv) => Promise<void>;
  log?: (message: string) => void;
}

/** Send through the relay: TLS 1.2 on port 25, IP-whitelisted, no auth. */
async function sendThroughRelay(message: SpeciesAddedEmail, env: NodeJS.ProcessEnv): Promise<void> {
  const transport = nodemailer.createTransport({
    host: env.SMTP_HOST,
    port: Number(env.SMTP_PORT || 25),
    secure: false,
    requireTLS: true,
    tls: { minVersion: "TLSv1.2" },
    connectionTimeout: SMTP_TIMEOUT_MS,
    greetingTimeout: SMTP_TIMEOUT_MS,
    socketTimeout: SMTP_TIMEOUT_MS,
  });
  await transport.sendMail({
    from: { name: env.SMTP_SENDER_NAME || "Bloom", address: env.SMTP_ADMIN_EMAIL || "noreply@bloom.salk.edu" },
    to: message.to,
    subject: message.subject,
    text: message.text,
  });
}

/** Email SPECIES_NOTIFY_EMAIL that a species was added. Returns whether a message was sent; never throws. */
export async function notifySpeciesAdded(
  species: { label: string; addedBy: string | null },
  deps: NotifyDeps = {}
): Promise<boolean> {
  const env = deps.env ?? process.env;
  const log = deps.log ?? ((m: string) => console.error(m));
  const to = recipients(env);
  if (!env.SMTP_HOST || to.length === 0) return false;
  try {
    const message = buildSpeciesAddedEmail({
      label: species.label,
      addedBy: species.addedBy,
      siteUrl: env.SITE_URL ?? "",
      to,
    });
    await (deps.send ?? sendThroughRelay)(message, env);
    return true;
  } catch (error) {
    log(
      `[species] Couldn't send the new-species email about ${species.label}: ${
        error instanceof Error ? error.message : "Unknown error"
      }`
    );
    return false;
  }
}
