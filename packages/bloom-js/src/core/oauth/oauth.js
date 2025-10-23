"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.decryptToken = exports.encryptToken = void 0;
const crypto_1 = require("crypto");
// Must be 256 bits (64 characters in hex)
const OAUTH_TOKEN_ENCRYPTION_KEY = process.env.OAUTH_TOKEN_ENCRYPTION_KEY;
// For AES, this is always 16
const IV_LENGTH = 16;
function encryptToken(text) {
    if (!OAUTH_TOKEN_ENCRYPTION_KEY)
        throw new Error("OAUTH_TOKEN_ENCRYPTION_KEY is not set");
    const iv = (0, crypto_1.randomBytes)(IV_LENGTH);
    const cipher = (0, crypto_1.createCipheriv)("aes-256-cbc", Buffer.from(OAUTH_TOKEN_ENCRYPTION_KEY, "hex"), iv);
    const encrypted = Buffer.concat([cipher.update(text), cipher.final()]);
    return iv.toString("hex") + ":" + encrypted.toString("hex");
}
exports.encryptToken = encryptToken;
function decryptToken(text) {
    if (!OAUTH_TOKEN_ENCRYPTION_KEY) {
        throw new Error("OAUTH_TOKEN_ENCRYPTION_KEY is not set");
    }
    const textParts = text.split(":");
    const ivRaw = textParts.shift();
    if (!ivRaw) {
        throw new Error("Encrypted text should have a valid IV part (before the first colon)");
    }
    const iv = Buffer.from(ivRaw, "hex");
    const encryptedText = Buffer.from(textParts.join(":"), "hex");
    const decipher = (0, crypto_1.createDecipheriv)("aes-256-cbc", Buffer.from(OAUTH_TOKEN_ENCRYPTION_KEY, "hex"), iv);
    const decrypted = Buffer.concat([
        decipher.update(encryptedText),
        decipher.final(),
    ]);
    return decrypted.toString();
}
exports.decryptToken = decryptToken;
