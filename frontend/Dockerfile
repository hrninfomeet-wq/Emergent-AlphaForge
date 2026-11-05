# Shared captured-source scope, byte-for-byte identical to the backend recipe.
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS identity
ARG ALPHAFORGE_BUILD_REVISION=""
ARG ALPHAFORGE_BUILD_DIRTY=""
COPY backend/ /source/backend/
COPY frontend/ /source/frontend/
COPY docker-compose.yml start-app.bat .dockerignore /source/
RUN python /source/backend/app/build_identity.py --root /source --out /build-manifest.json --revision "$ALPHAFORGE_BUILD_REVISION" --dirty "$ALPHAFORGE_BUILD_DIRTY"

# Multi-stage: build with node, serve with nginx
FROM node:20-alpine AS build

ARG REACT_APP_BACKEND_URL=http://localhost:8001
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
ENV REACT_APP_CHART_STREAM_BASE=/api

WORKDIR /app
COPY frontend/package.json frontend/yarn.lock ./
RUN yarn install --frozen-lockfile --network-timeout 600000

COPY frontend/ .
COPY --from=identity /build-manifest.json /app/src/build-manifest.json
RUN yarn build

FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
