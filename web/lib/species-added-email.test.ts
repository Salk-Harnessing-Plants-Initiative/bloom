/** The new-species email: who it goes to, what it says, and that it never gets in the way. */

import { describe, expect, it, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  USER_LOOKUP_PAGE_SIZE,
  buildSpeciesAddedEmail,
  notifySpeciesAdded,
  recipientEmails,
} from "./species-added-email";

type User = { email?: string; app_metadata?: Record<string, unknown> };

/** A service-role client whose auth.admin.listUsers pages through `users`. */
function adminClient(users: User[], error: { message: string } | null = null) {
  const listUsers = vi.fn(async ({ page, perPage }: { page: number; perPage: number }) =>
    error
      ? { data: { users: [] }, error }
      : { data: { users: users.slice((page - 1) * perPage, page * perPage) }, error: null }
  );
  return { client: { auth: { admin: { listUsers } } } as unknown as SupabaseClient, listUsers };
}

const ME = { email: "me@salk.edu", app_metadata: { notify_new_species: true } };
const ENV = {
  SMTP_HOST: "relay.salk.edu",
  SITE_URL: "https://bloom.salk.edu",
} as unknown as NodeJS.ProcessEnv;

describe("buildSpeciesAddedEmail", () => {
  it("names the species, who added it, how to add its image and the Species page", () => {
    const email = buildSpeciesAddedEmail({
      label: "Pennycress (Thlaspi arvense)",
      addedBy: "scientist@salk.edu",
      siteUrl: "https://bloom.salk.edu/",
      to: ["me@salk.edu"],
    });
    expect(email.to).toEqual(["me@salk.edu"]);
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

describe("recipientEmails", () => {
  it("is everyone flagged notify_new_species, and nobody else", async () => {
    const { client } = adminClient([
      ME,
      { email: "admin@salk.edu", app_metadata: { is_admin: true } },
      { email: "user@salk.edu", app_metadata: {} },
      { email: "off@salk.edu", app_metadata: { notify_new_species: "yes" } },
      { app_metadata: { notify_new_species: true } },
    ]);
    expect(await recipientEmails(client)).toEqual(["me@salk.edu"]);
  });

  it("reads every page of users", async () => {
    const users: User[] = Array.from({ length: USER_LOOKUP_PAGE_SIZE + 5 }, (_, i) => ({ email: `u${i}@salk.edu` }));
    users[USER_LOOKUP_PAGE_SIZE + 2] = ME;
    const { client, listUsers } = adminClient(users);
    expect(await recipientEmails(client)).toEqual(["me@salk.edu"]);
    expect(listUsers).toHaveBeenCalledTimes(2);
  });
});

describe("notifySpeciesAdded", () => {
  const species = { label: "Pennycress (Thlaspi arvense)", addedBy: "scientist@salk.edu" };

  it("sends one email to the flagged users", async () => {
    const send = vi.fn(async (_message: { to: string[]; subject: string }) => {});
    const { client } = adminClient([ME]);
    expect(await notifySpeciesAdded(species, { env: ENV, adminClient: () => client, send })).toBe(true);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({
      to: ["me@salk.edu"],
      subject: "New species in Bloom: Pennycress (Thlaspi arvense)",
    });
  });

  it("sends nothing, and looks nobody up, without a mail relay (dev)", async () => {
    const send = vi.fn();
    const adminClientFn = vi.fn();
    expect(
      await notifySpeciesAdded(species, { env: {} as unknown as NodeJS.ProcessEnv, adminClient: adminClientFn, send })
    ).toBe(false);
    expect(adminClientFn).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });

  it("says so, and sends nothing, when nobody is flagged", async () => {
    const send = vi.fn();
    const log = vi.fn();
    const { client } = adminClient([{ email: "user@salk.edu" }]);
    expect(await notifySpeciesAdded(species, { env: ENV, adminClient: () => client, send, log })).toBe(false);
    expect(send).not.toHaveBeenCalled();
    expect(log).toHaveBeenCalledWith(
      "[species] Nobody is flagged notify_new_species to hear that Pennycress (Thlaspi arvense) was added."
    );
  });

  it("logs a failure and never throws, whether the lookup or the relay fails", async () => {
    const log = vi.fn();
    const failingLookup = adminClient([], { message: "invalid JWT" }).client;
    expect(await notifySpeciesAdded(species, { env: ENV, adminClient: () => failingLookup, log })).toBe(false);
    expect(log).toHaveBeenLastCalledWith(
      "[species] Couldn't send the new-species email about Pennycress (Thlaspi arvense): Couldn't list Bloom's users: invalid JWT"
    );

    const send = vi.fn(async () => {
      throw new Error("connect ETIMEDOUT");
    });
    const { client } = adminClient([ME]);
    expect(await notifySpeciesAdded(species, { env: ENV, adminClient: () => client, send, log })).toBe(false);
    expect(log).toHaveBeenLastCalledWith(
      "[species] Couldn't send the new-species email about Pennycress (Thlaspi arvense): connect ETIMEDOUT"
    );
  });
});
