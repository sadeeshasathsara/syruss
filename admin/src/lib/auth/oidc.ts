import * as o from "oauth4webapi";
import { authConfig, safeReturnTo } from "./config";
import { identityOf, type Session } from "./session";

type Mode = "signin" | "signup";

const insecure = () => authConfig().issuer.startsWith("http://"); // local development only

let metadata: { at: number; value: o.AuthorizationServer } | null = null;

async function server(): Promise<o.AuthorizationServer> {
  if (metadata && Date.now() - metadata.at < 10 * 60_000) return metadata.value;
  const issuer = new URL(authConfig().issuer);
  const response = await o.discoveryRequest(issuer, { algorithm: "oidc", [o.allowInsecureRequests]: insecure() });
  const value = await o.processDiscoveryResponse(issuer, response);
  metadata = { at: Date.now(), value };
  return value;
}

const client = (): o.Client => ({ client_id: authConfig().clientId });
const clientAuth = () => o.ClientSecretPost(authConfig().clientSecret);
export const redirectUri = () => `${authConfig().appOrigin}/api/auth/callback`;

export type Transaction = { verifier: string; state: string; nonce: string; returnTo: string };

/** The address to send the person to (Keycloak's sign-in or registration page) and what to remember meanwhile. */
export async function beginLogin(opts: { mode: Mode; returnTo?: string | null; idpHint?: string | null; reauth?: boolean }) {
  const as = await server();
  const verifier = o.generateRandomCodeVerifier();
  const tx: Transaction = {
    verifier, state: o.generateRandomState(), nonce: o.generateRandomNonce(), returnTo: safeReturnTo(opts.returnTo, "/"),
  };
  // Keycloak's own registration page lives next to its authorization endpoint.
  const base = opts.mode === "signup" ? as.authorization_endpoint!.replace(/\/auth$/, "/registrations") : as.authorization_endpoint!;
  const url = new URL(base);
  url.searchParams.set("client_id", authConfig().clientId);
  url.searchParams.set("redirect_uri", redirectUri());
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid profile email");
  url.searchParams.set("code_challenge", await o.calculatePKCECodeChallenge(verifier));
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("state", tx.state);
  url.searchParams.set("nonce", tx.nonce);
  if (opts.idpHint === "google") url.searchParams.set("kc_idp_hint", "google");
  // Asked to sign in again (before a risky action): Keycloak must ask for the credentials, not reuse its session.
  if (opts.reauth) {
    url.searchParams.set("prompt", "login");
    url.searchParams.set("max_age", "0");
  }
  return { url: url.toString(), tx };
}

function toSession(result: o.TokenEndpointResponse): Session {
  const accessToken = result.access_token;
  const { sub, name, email } = identityOf(accessToken);
  return {
    sub, name, email, accessToken,
    refreshToken: result.refresh_token ?? "",
    idToken: result.id_token,
    accessExp: Math.floor(Date.now() / 1000) + (result.expires_in ?? 300),
  };
}

/** Completes the sign-in: checks state, exchanges the code (with the PKCE verifier) and the nonce. */
export async function completeLogin(currentUrl: URL, tx: Transaction): Promise<Session> {
  const as = await server();
  const params = o.validateAuthResponse(as, client(), currentUrl.searchParams, tx.state);
  const response = await o.authorizationCodeGrantRequest(as, client(), clientAuth(), params, redirectUri(), tx.verifier, {
    [o.allowInsecureRequests]: insecure(),
  });
  const result = await o.processAuthorizationCodeResponse(as, client(), response, { expectedNonce: tx.nonce, requireIdToken: true });
  return toSession(result);
}

const inflight = new Map<string, Promise<Session | null>>();

/** A new access token from the refresh token. Concurrent callers share one request (refresh tokens rotate). */
export function refresh(session: Session): Promise<Session | null> {
  const existing = inflight.get(session.refreshToken);
  if (existing) return existing;
  const run = (async () => {
    try {
      const as = await server();
      const response = await o.refreshTokenGrantRequest(as, client(), clientAuth(), session.refreshToken, {
        [o.allowInsecureRequests]: insecure(),
      });
      const result = await o.processRefreshTokenResponse(as, client(), response);
      return toSession({ ...result, refresh_token: result.refresh_token ?? session.refreshToken, id_token: result.id_token ?? session.idToken });
    } catch {
      return null; // the Keycloak session ended or the token was revoked: sign in again
    } finally {
      setTimeout(() => inflight.delete(session.refreshToken), 5000);
    }
  })();
  inflight.set(session.refreshToken, run);
  return run;
}

export const needsRefresh = (s: Session) => s.accessExp - Math.floor(Date.now() / 1000) < 30;

export async function logoutUrl(idToken?: string): Promise<string> {
  const as = await server();
  const url = new URL(as.end_session_endpoint!);
  if (idToken) url.searchParams.set("id_token_hint", idToken);
  url.searchParams.set("client_id", authConfig().clientId);
  url.searchParams.set("post_logout_redirect_uri", `${authConfig().siteOrigin}/signed-out`);
  return url.toString();
}
