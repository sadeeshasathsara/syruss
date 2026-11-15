/**
 * Seals a secret in the browser so only the mail service can open it: the mailbox password never reaches this app's server or
 * Kafka in the clear. The mail service publishes the public key it wants secrets sealed to (and its fingerprint); this is its
 * counterpart of mail/sealing.py `box_seal`: an ephemeral X25519 key, HKDF-SHA256, AES-256-GCM.
 *
 *   result = base64( ephemeral public key (32) | nonce (12) | ciphertext + tag )
 *   key    = HKDF-SHA256(secret, salt = ephemeral public key | recipient public key, info = "syruss-mail-seal-v1")
 *   the info is also the AES-GCM associated data
 *
 * Needs a browser with X25519 in WebCrypto (Chrome 133+, Firefox 130+, Safari 17+); `canSeal()` says whether this one has it.
 */
const INFO = new TextEncoder().encode("syruss-mail-seal-v1");

export function b64decode(text: string): Uint8Array {
  const raw = atob(text);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export function b64encode(bytes: Uint8Array): string {
  let raw = "";
  for (const b of bytes) raw += String.fromCharCode(b);
  return btoa(raw);
}

const buffer = (bytes: Uint8Array): ArrayBuffer => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;

function join(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

/** Whether this browser can seal (X25519 in WebCrypto). */
export async function canSeal(): Promise<boolean> {
  try {
    await crypto.subtle.generateKey({ name: "X25519" }, true, ["deriveBits"]);
    return true;
  } catch {
    return false;
  }
}

/** `secrets` sealed to the mail service's public key (base64, as the directory publishes it). */
export async function sealForMail(recipientPublicKey: string, secrets: Record<string, unknown>): Promise<string> {
  const recipient = b64decode(recipientPublicKey);
  if (recipient.length !== 32) throw new Error("The mail service's key isn't valid.");
  const subtle = crypto.subtle;
  const ephemeral = (await subtle.generateKey({ name: "X25519" }, true, ["deriveBits"])) as CryptoKeyPair;
  const ephemeralPublic = new Uint8Array(await subtle.exportKey("raw", ephemeral.publicKey));
  const peer = await subtle.importKey("raw", buffer(recipient), { name: "X25519" }, false, []);
  const shared = await subtle.deriveBits({ name: "X25519", public: peer }, ephemeral.privateKey, 256);
  const hkdf = await subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
  const key = await subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: buffer(join(ephemeralPublic, recipient)), info: buffer(INFO) },
    hkdf, { name: "AES-GCM", length: 256 }, false, ["encrypt"],
  );
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = new Uint8Array(await subtle.encrypt(
    { name: "AES-GCM", iv: buffer(nonce), additionalData: buffer(INFO) }, key, buffer(new TextEncoder().encode(JSON.stringify(secrets))),
  ));
  return b64encode(join(ephemeralPublic, nonce, ciphertext));
}
