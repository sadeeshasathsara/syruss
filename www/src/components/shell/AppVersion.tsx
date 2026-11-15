/**
 * The version of this app, as the release made it: the build is given NEXT_PUBLIC_APP_VERSION by the release job
 * (.github/workflows/package.yml, from the changesets). On production it is `0.9.0`; the test and dev environments show
 * their own (`0.16.0-dev.1`), so what you see tells you which release you are on. A build made by hand has none (or the
 * Dockerfile's placeholder `dev`), and shows nothing.
 */
const VERSION = process.env.NEXT_PUBLIC_APP_VERSION?.trim().replace(/^v/, "");

export function AppVersion() {
  if (!VERSION || VERSION === "dev") return null;
  return <p className="mt-auto px-2.5 pt-2 text-meta text-faint [overflow-wrap:anywhere]">Syruss {VERSION}</p>;
}
