/**
 * A full browser navigation, for addresses that aren't pages of this app: the sign-in routes (/api/auth/*), which the
 * server answers with a redirect to Keycloak. A client-side route change can't follow those, so this loads the page.
 */
export function loadPage(path: string): void {
  window.location.assign(new URL(path, window.location.origin).href);
}
