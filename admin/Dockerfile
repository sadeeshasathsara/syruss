# ==============================================================================
# Syruss System Admin (admin/) Dockerfile - Next.js Standalone
# ==============================================================================
FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_ADMIN_VERSION=dev
ENV NEXT_PUBLIC_ADMIN_VERSION=${NEXT_PUBLIC_ADMIN_VERSION}
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3200
ENV HOSTNAME="0.0.0.0"

# The server runs on node alone: no package managers in the running image
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /opt/yarn-v* \
    /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/yarn /usr/local/bin/yarnpkg /usr/local/bin/corepack \
    && apk upgrade --no-cache

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Automatically leverage output traces to reduce image size
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3200

CMD ["node", "server.js"]
