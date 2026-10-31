import type { Tone } from "@/lib/format";
import type { IntakeChannelInfo, IntakeKeyInfo } from "@/lib/types";

export const TYPE_LABEL: Record<string, string> = { "image/jpeg": "JPEG", "image/png": "PNG", "image/tiff": "TIFF", "image/webp": "WebP" };
export const ALL_TYPES = Object.keys(TYPE_LABEL);

export const KIND_LABEL: Record<IntakeChannelInfo["kind"], string> = { api: "API", email: "Email", sftp: "SFTP" };

/** How a channel is doing, in one word and a colour. */
export function channelHealth(c: IntakeChannelInfo): { tone: Tone; label: string } {
  if (c.status === "paused") return { tone: "neutral", label: "Paused" };
  if (!c.keys.some((k) => k.state === "active" || k.state === "retiring")) return { tone: "warning", label: "Needs a key" };
  if (c.stats.rejected_24h > 0 && c.stats.accepted_24h === 0) return { tone: "negative", label: "Refusing documents" };
  return { tone: "positive", label: "Ready" };
}

export const KEY_STATE: Record<IntakeKeyInfo["state"], { tone: Tone; label: string }> = {
  active: { tone: "positive", label: "Active" },
  retiring: { tone: "warning", label: "Being replaced" },
  expired: { tone: "neutral", label: "Expired" },
  revoked: { tone: "neutral", label: "Revoked" },
};

/** The size a sender may send, in words. */
export function megabytes(mb: number | null | undefined, fallback: number): string {
  return `${mb ?? fallback} MB`;
}
