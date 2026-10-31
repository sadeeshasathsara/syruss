// The sign-in code in site/src/lib/auth and www/src/lib/auth must stay identical (one implementation, two apps).
import { readFileSync } from "node:fs";

const files = ["lib/auth/config.ts", "lib/auth/session.ts", "lib/auth/oidc.ts", "lib/auth/device.ts", "app/api/auth/[action]/route.ts"];
let drift = 0;
for (const f of files) {
  const a = readFileSync(new URL(`../src/${f}`, import.meta.url), "utf8");
  const b = readFileSync(new URL(`../../www/src/${f}`, import.meta.url), "utf8");
  if (a !== b) {
    console.error(`differs: ${f}`);
    drift++;
  }
}
if (drift) process.exit(1);
console.log("sign-in code is identical in site/ and www/");
