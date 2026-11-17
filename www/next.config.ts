import fs from "fs";
import path from "path";
import type { NextConfig } from "next";

// The browser reaches the core API only through the app's own /api/v1 proxy (src/app/api/v1), which adds the
// signed-in person's token on the server. Live updates connect straight to the API with a single-use ticket.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";
const api = new URL(API_ORIGIN);

/**
 * Resolves the application version:
 * 1. Build-time argument / environment variable (used by Docker & CI releases).
 * 2. In local development: dynamically reads the current version of www from the monorepo's
 *    _changelog/ markdown file corresponding to the environment (dev.md, test.md, or main.md).
 */
function getAppVersion(): string | undefined {
  const envVal = process.env.NEXT_PUBLIC_APP_VERSION?.trim();
  if (envVal && envVal !== "dev") {
    return envVal;
  }
  const env = (process.env.ENV || process.env.APP_ENV || "").toLowerCase();
  const file = env === "production" || env === "main" ? "main.md"
    : env === "test" || env === "staging" ? "test.md"
    : "dev.md";

  const candidates = [
    path.resolve(process.cwd(), "..", "_changelog", file),
    path.resolve(process.cwd(), "_changelog", file),
  ];
  for (const cand of candidates) {
    if (fs.existsSync(cand)) {
      try {
        const text = fs.readFileSync(cand, "utf-8");
        // Look for table line: | **www** | `1.0.0-dev.1` | ...
        const line = text.split("\n").find((l) => l.includes("**www**"));
        if (line) {
          const parts = line.split("|").map((s) => s.trim());
          if (parts[2]) {
            return parts[2].replace(/`/g, "").trim();
          }
        }
        // Fallback heading: ### www (1.0.0-dev.1)
        const heading = text.split("\n").find((l) => l.startsWith("### www"));
        if (heading) {
          const m = heading.match(/###\s+www\s+\(([^)]+)\)/);
          if (m) return m[1].trim();
        }
      } catch {
        // ignore read error
      }
    }
  }
  return undefined;
}

const resolvedVersion = getAppVersion();

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    // Tree-shake large packages so only the icons/components actually imported are
    // included in each page's compile graph. Cuts first-load compile time noticeably
    // for pages that use Lucide icons or Radix UI primitives.
    optimizePackageImports: [
      "lucide-react",
      "recharts",
      "@radix-ui/react-avatar",
      "@radix-ui/react-dialog",
      "@radix-ui/react-dropdown-menu",
      "@radix-ui/react-progress",
      "@radix-ui/react-scroll-area",
      "@radix-ui/react-select",
      "@radix-ui/react-separator",
      "@radix-ui/react-slot",
      "@radix-ui/react-tabs",
      "@radix-ui/react-tooltip",
      "@assistant-ui/react",
      "@assistant-ui/react-markdown",
    ],
  },
  env: {
    ...(resolvedVersion ? { NEXT_PUBLIC_APP_VERSION: resolvedVersion } : {}),
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          // Suppress "unload is not allowed" violations from Chrome extensions
          { key: "Permissions-Policy", value: "unload=(), camera=(), microphone=(), geolocation=(), payment=()" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          ...(process.env.NODE_ENV === "production" ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }] : []),
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
  images: {
    remotePatterns: [
      { protocol: api.protocol.replace(":", "") as "http" | "https", hostname: api.hostname },
    ],
  },
};

export default nextConfig;
