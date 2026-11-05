# syntax=docker/dockerfile:1.7
# Build context is the repository root (compose: context: .., dockerfile: frontend/Dockerfile).
FROM node:24-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
ARG VITE_AUTH_ENABLED=true
ARG VITE_KEYCLOAK_URL=http://localhost:8081
ENV VITE_AUTH_ENABLED=$VITE_AUTH_ENABLED VITE_KEYCLOAK_URL=$VITE_KEYCLOAK_URL
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
