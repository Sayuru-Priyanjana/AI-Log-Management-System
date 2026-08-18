# Build the bundle, then throw the toolchain away and serve the result.
#
#   docker build -t log-ui ./ui
#
# The agent's address is *not* baked in. VITE_AGENT_URL is set to the empty
# string, which the API layer reads as "same origin", and nginx proxies /api to
# the agent container. One less thing to get wrong per environment, and no CORS.

FROM node:22-alpine AS build

WORKDIR /app

# package.json changes far less often than src/, so dependencies install once
# and survive every subsequent edit to a component.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ENV VITE_AGENT_URL=""
RUN npm run build


FROM nginx:1.27-alpine AS serve

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
