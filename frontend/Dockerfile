FROM node:22-slim AS build
RUN corepack enable && corepack prepare pnpm@10.17.1 --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ENV LOGICCRITIC_BACKEND_URL=http://api:8000 NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:22-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=80 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 80
CMD ["node", "server.js"]
