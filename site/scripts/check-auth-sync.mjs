// The sign-in code in site/, www/ and admin/ (src/lib/auth and the auth route) must stay identical: one implementation, three apps.
import { readFileSync } from "node:fs";

const files = ["lib/auth/config.ts", "lib/auth/session.ts", "lib/auth/oidc.ts", "lib/auth/device.ts", "app/api/auth/[action]/route.ts"];
const apps = ["www", "admin"];
let drift = 0;
for (const f of files) {
  const a = readFileSync(new URL(`../src/${f}`, import.meta.url), "utf8");
  for (const app of apps) {
    const b = readFileSync(new URL(`../../${app}/src/${f}`, import.meta.url), "utf8");
    if (a !== b) {
      console.error(`differs from site/: ${app}/src/${f}`);
      drift++;
    }
  }
}
if (drift) process.exit(1);
console.log(`sign-in code is identical in site/, ${apps.map((a) => `${a}/`).join(" and ")}`);
