import { randomBytes, createCipheriv, createDecipheriv } from "crypto";

// Must be 256 bits (64 characters in hex)
const OAUTH_TOKEN_ENCRYPTION_KEY = process.env.OAUTH_TOKEN_ENCRYPTION_KEY;

// For AES, this is always 16
const IV_LENGTH = 16;

export function encryptToken(text: string): string {
  if (!OAUTH_TOKEN_ENCRYPTION_KEY)
    throw new Error("OAUTH_TOKEN_ENCRYPTION_KEY is not set");
  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv(
    "aes-256-cbc",
    Buffer.from(OAUTH_TOKEN_ENCRYPTION_KEY, "hex"),
    iv
  );
  const encrypted = Buffer.concat([cipher.update(text), cipher.final()]);
  return iv.toString("hex") + ":" + encrypted.toString("hex");
}

export function decryptToken(text: string): string {
  if (!OAUTH_TOKEN_ENCRYPTION_KEY) {
    throw new Error("OAUTH_TOKEN_ENCRYPTION_KEY is not set");
  }
  const textParts = text.split(":");
  const ivRaw = textParts.shift();
  if (!ivRaw) {
    throw new Error(
      "Encrypted text should have a valid IV part (before the first colon)"
    );
  }
  const iv = Buffer.from(ivRaw, "hex");
  const encryptedText = Buffer.from(textParts.join(":"), "hex");
  const decipher = createDecipheriv(
    "aes-256-cbc",
    Buffer.from(OAUTH_TOKEN_ENCRYPTION_KEY, "hex"),
    iv
  );
  const decrypted = Buffer.concat([
    decipher.update(encryptedText),
    decipher.final(),
  ]);
  return decrypted.toString();
}
