# syntax=docker/dockerfile:1

# Simply Notes — https://notes.noobius.in
#
# Next.js standalone output: the runtime image carries only the server, the
# traced subset of node_modules, and the static assets. No build toolchain, no
# package manager, no source.

# ---------------------------------------------------------------- dependencies
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --------------------------------------------------------------------- builder
FROM node:22-alpine AS builder
WORKDIR /app

# NEXT_PUBLIC_* values are inlined into the client bundle at build time, so they
# have to arrive here rather than at `docker run`.
ARG NEXT_PUBLIC_SITE_URL=https://notes.noobius.in
ARG NEXT_PUBLIC_GA_MEASUREMENT_ID=""
ENV NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_GA_MEASUREMENT_ID=$NEXT_PUBLIC_GA_MEASUREMENT_ID \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---------------------------------------------------------------------- runner
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 --ingroup nodejs nextjs

# server.js and its traced dependencies.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# The standalone server does not copy these itself.
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
