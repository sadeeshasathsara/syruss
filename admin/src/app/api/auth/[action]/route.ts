import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { authConfig, safeReturnTo } from "@/lib/auth/config";
import { checkDevice, ensureDeviceToken } from "@/lib/auth/device";
import { beginLogin, completeLogin, logoutUrl, refresh, type Transaction } from "@/lib/auth/oidc";
import { clearSealed, clearSession, readSealed, readSession, writeSealed, writeSession } from "@/lib/auth/session";

// login, callback and logout for this app. The browser only ever holds an encrypted, httpOnly cookie.

const TX_MAX_AGE = 600;
const txName = () => `${authConfig().cookieName}_tx`;

function go(path: string): NextResponse {
  return NextResponse.redirect(new URL(path, authConfig().appOrigin), { status: 303 });
}

/** A sign-in problem is explained on the site's front page: sending it back into the app could loop forever. */
function failed(reason: "expired" | "signin"): NextResponse {
  return NextResponse.redirect(new URL(`/?error=${reason}`, authConfig().siteOrigin), { status: 303 });
}

async function login(request: NextRequest) {
  const q = request.nextUrl.searchParams;
  const mode = q.get("mode") === "signup" ? "signup" : "signin";
  const { url, tx } = await beginLogin({ mode, returnTo: q.get("returnTo"), idpHint: q.get("idp"), reauth: q.get("reauth") === "1" });
  await writeSealed(await cookies(), txName(), tx, TX_MAX_AGE);
  return NextResponse.redirect(url, { status: 303 });
}

async function callback(request: NextRequest) {
  const jar = await cookies();
  const tx = await readSealed<Transaction>(jar, txName());
  clearSealed(jar, txName());
  if (!tx) return failed("expired");
  const error = request.nextUrl.searchParams.get("error");
  if (error) return error === "access_denied" ? NextResponse.redirect(new URL("/", authConfig().siteOrigin), { status: 303 }) : failed("signin");
  let session;
  try {
    // Behind a proxy request.url may carry an internal host; the query string is what matters.
    const current = new URL(`${authConfig().appOrigin}/api/auth/callback${request.nextUrl.search}`);
    session = await completeLogin(current, tx);
    await writeSession(session);
  } catch {
    return failed("signin");
  }
  const returnTo = safeReturnTo(tx.returnTo, "/");
  // Is this a browser the person has confirmed? A new one is sent to the site for the code before anything opens.
  const ip = (request.headers.get("x-forwarded-for") ?? "").split(",")[0].trim();
  const device = await checkDevice(session, await ensureDeviceToken(), request.headers.get("user-agent") ?? "", ip);
  if (device === "challenge") {
    const { siteOrigin, appOrigin } = authConfig();
    const next = siteOrigin === appOrigin ? returnTo : `/continue?next=${encodeURIComponent(returnTo)}`;
    return NextResponse.redirect(new URL(`/verify-device?next=${encodeURIComponent(next)}`, siteOrigin), { status: 303 });
  }
  return go(returnTo);
}

async function renew(request: NextRequest) {
  const returnTo = safeReturnTo(request.nextUrl.searchParams.get("returnTo"), "/");
  const session = await readSession();
  const fresh = session ? await refresh(session) : null;
  if (!fresh) {
    await clearSession();
    return go(`/api/auth/login?returnTo=${encodeURIComponent(returnTo)}`);
  }
  await writeSession(fresh);
  return go(returnTo);
}

async function logout(request: NextRequest) {
  // POST only, from this app's own pages: a link on another site can't sign someone out.
  if (request.headers.get("origin") !== authConfig().appOrigin) return new NextResponse("Not allowed", { status: 403 });
  const session = await readSession();
  await clearSession();
  return NextResponse.redirect(await logoutUrl(session?.idToken), { status: 303 });
}

/** Called by the signed-out page: whichever app a person signed out from, this app's own session ends too. */
async function clear(request: NextRequest) {
  if (request.headers.get("origin") !== authConfig().appOrigin) return new NextResponse("Not allowed", { status: 403 });
  await clearSession();
  return new NextResponse(null, { status: 204 });
}

export async function GET(request: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action === "login") return login(request);
  if (action === "callback") return callback(request);
  if (action === "refresh") return renew(request);
  return new NextResponse("Not found", { status: 404 });
}

export async function POST(request: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action === "logout") return logout(request);
  if (action === "clear") return clear(request);
  return new NextResponse("Not found", { status: 404 });
}
