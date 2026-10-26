/** The new-species email: who it goes to, what it says, and that it never gets in the way. */

import { describe, expect, it, vi } from "vitest";
import { buildSpeciesAddedEmail, notifySpeciesAdded, recipients } from "./species-added-email";

const env = (values: Record<string, string>) => values as unknown as NodeJS.ProcessEnv;
const ENV = env({
  SMTP_HOST: "relay.salk.edu",
  SITE_URL: "https://bloom.salk.edu",
  SPECIES_NOTIFY_EMAIL: "bfernando@salk.edu",
});

describe("buildSpeciesAddedEmail", () => {
  it("names the species, who added it, how to add its image and the Species page", () => {
    const email = buildSpeciesAddedEmail({
      label: "Pennycress (Thlaspi arvense)",
      addedBy: "scientist@salk.edu",
      siteUrl: "https://bloom.salk.edu/",
      to: ["bfernando@salk.edu"],
    });
    expect(email.to).toEqual(["bfernando@salk.edu"]);
    expect(email.subject).toBe("New species in Bloom: Pennycress (Thlaspi arvense)");
    expect(email.text).toContain("Pennycress (Thlaspi arvense) was added to Bloom by scientist@salk.edu.");
    expect(email.text).toContain("placeholder image");
    expect(email.text).toContain("species-illustrations");
    expect(email.text).toContain("Species page: https://bloom.salk.edu/app/species");
  });

  it("leaves out who added it when that's unknown", () => {
    const email = buildSpeciesAddedEmail({ label: "Rice (Oryza sativa)", addedBy: null, siteUrl: "", to: [] });
    expect(email.text.split("\n")[0]).toBe("Rice (Oryza sativa) was added to Bloom.");
  });
});

describe("recipients", () => {
  it("is SPECIES_NOTIFY_EMAIL, which may list several addresses", () => {
    expect(recipients(ENV)).toEqual(["bfernando@salk.edu"]);
    expect(recipients(env({ SPECIES_NOTIFY_EMAIL: " a@salk.edu, b@salk.edu ,," }))).toEqual([
      "a@salk.edu",
      "b@salk.edu",
    ]);
    expect(recipients(env({}))).toEqual([]);
  });
});

describe("notifySpeciesAdded", () => {
  const species = { label: "Pennycress (Thlaspi arvense)", addedBy: "scientist@salk.edu" };

  it("sends one email to SPECIES_NOTIFY_EMAIL", async () => {
    const send = vi.fn(async (_message: { to: string[]; subject: string }) => {});
    expect(await notifySpeciesAdded(species, { env: ENV, send })).toBe(true);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({
      to: ["bfernando@salk.edu"],
      subject: "New species in Bloom: Pennycress (Thlaspi arvense)",
    });
  });

  it("sends nothing without a mail relay or a recipient (dev)", async () => {
    const send = vi.fn();
    expect(await notifySpeciesAdded(species, { env: env({ SPECIES_NOTIFY_EMAIL: "bfernando@salk.edu" }), send })).toBe(false);
    expect(await notifySpeciesAdded(species, { env: env({ SMTP_HOST: "relay.salk.edu" }), send })).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });

  it("logs a failure and never throws", async () => {
    const log = vi.fn();
    const send = vi.fn(async () => {
      throw new Error("connect ETIMEDOUT");
    });
    expect(await notifySpeciesAdded(species, { env: ENV, send, log })).toBe(false);
    expect(log).toHaveBeenCalledWith(
      "[species] Couldn't send the new-species email about Pennycress (Thlaspi arvense): connect ETIMEDOUT"
    );
  });
});
