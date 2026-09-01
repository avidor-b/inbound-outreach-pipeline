# syntax=docker/dockerfile:1.7
#
# Outreach frontend — Next.js 16 in standalone output mode. See
# docs/cloud-architecture.md.
#
# Build-time args:
#   NEXT_PUBLIC_BACKEND_URL  Baked into the client bundle. In production
#                            set this to the ALB/public URL (e.g.
#                            https://outreach.example.com). In dev compose
#                            it can stay http://localhost:8000.
#
# Three stages:
#   deps    install npm dependencies once, cacheable by package*.json
#   builder run `next build` against the deps
#   runtime distroless-ish node image copying just the standalone bundle

# ---------- deps ----------
FROM node:26.8.1-alpine AS deps
WORKDIR /app
# scripts/ contains the postinstall patch-ag-ui.js runner; it must be
# present before npm ci or the postinstall hook fails "MODULE_NOT_FOUND".
COPY package.json package-lock.json* ./
COPY scripts ./scripts
RUN npm ci

# ---------- builder ----------
FROM node:26.8.1-alpine AS builder
WORKDIR /app

ARG NEXT_PUBLIC_BACKEND_URL="http://localhost:8000"
ENV NEXT_PUBLIC_BACKEND_URL=${NEXT_PUBLIC_BACKEND_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Standalone output (configured in next.config.ts) drops a minimal
# server + its transitive deps into .next/standalone.
RUN npm run build

# ---------- runtime ----------
FROM node:26.8.1-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# .next/standalone embeds everything needed to run the server. .next/static
# stays outside standalone by design — copy both. public/ is optional
# (this project doesn't have one); create an empty dir pre-emptively so
# Next's runtime doesn't fail when it probes for it.
RUN mkdir -p ./public && chown nextjs:nodejs ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --chown=nextjs:nodejs LICENSE ./LICENSE

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --spider -q http://127.0.0.1:${PORT}/ || exit 1
