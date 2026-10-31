"use server";

import { redirect } from "next/navigation";
import type { FormState } from "@/app/onboarding/actions";
import { ApiError, api, requireSession } from "@/lib/api";
import { safeReturnTo } from "@/lib/auth/config";

export type DeviceStatus = {
  status: "trusted" | "challenge" | "unknown";
  device: string;
  email: string;
  resend_in: number;
  code_sent: boolean;
  code_valid_minutes: number;
  trust_days: number;
};

const here = (next: string) => `/verify-device?next=${encodeURIComponent(next)}`;

export async function deviceStatus(next: string): Promise<DeviceStatus> {
  return api<DeviceStatus>(await requireSession(here(next)), "/me/device", { returnTo: here(next) });
}

/** Emails a new code for this browser. */
export async function sendDeviceCode(): Promise<{ status?: DeviceStatus; error?: string }> {
  const session = await requireSession("/verify-device");
  try {
    return { status: await api<DeviceStatus>(session, "/me/device/send", { body: {}, returnTo: "/verify-device" }) };
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    if (e.status === 429) return { status: await api<DeviceStatus>(session, "/me/device", { returnTo: "/verify-device" }), error: e.message };
    return { error: e.message };
  }
}

/** The code, and whether to remember this browser. Then on to where the person was going. */
export async function confirmDevice(_: FormState, data: FormData): Promise<FormState> {
  const next = safeReturnTo(String(data.get("next") ?? ""), "/continue");
  const session = await requireSession(here(next));
  const code = String(data.get("code") ?? "").replace(/\D/g, "");
  if (code.length < 6) return { error: "Enter the 6-digit code from the email." };
  try {
    await api(session, "/me/device/confirm", { body: { code, remember: data.get("remember") === "on" }, returnTo: here(next) });
  } catch (e) {
    if (e instanceof ApiError) return { error: e.message };
    throw e;
  }
  redirect(next);
}
