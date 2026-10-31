import type { NextConfig } from "next";

// The browser reaches the core API only through the app's own /api/v1 proxy (src/app/api/v1), which adds the
// signed-in person's token on the server. Live updates connect straight to the API with a single-use ticket.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";
const api = new URL(API_ORIGIN);

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
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
