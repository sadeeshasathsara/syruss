import type { NextConfig } from "next";

const origin = (value: string | undefined, fallback: string) => new URL(value ?? fallback).origin;

/** Production only: the development server's hot reload needs looser rules. Pages here are prerendered, so scripts can't carry a nonce. */
function policy(): string {
  const forms = ["'self'", origin(process.env.WORKSPACE_APP_URL, "http://localhost:3000"), origin(process.env.OIDC_ISSUER, "http://localhost:8080")];
  return [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "media-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    `form-action ${forms.join(" ")}`,
    "frame-ancestors 'none'",
  ].join("; ");
}

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          ...(process.env.NODE_ENV === "production"
            ? [
                { key: "Content-Security-Policy", value: policy() },
                { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
              ]
            : []),
        ],
      },
    ];
  },
};

export default nextConfig;
