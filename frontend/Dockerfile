# =============================================================================
# Frontend Dockerfile — builds Vite static files, serves via nginx
# =============================================================================
# Multi-stage build: Node.js for building, nginx for serving (small image)

# ── Stage 1: Build ──
FROM node:20-slim AS builder
WORKDIR /app

# Copy package files first for layer caching
COPY package.json package-lock.json ./
# --legacy-peer-deps: vite@8 vs @vitejs/plugin-react peer dep range mismatch
RUN npm ci --legacy-peer-deps --silent

# Copy source and build
COPY . .
# Build the Vite frontend — output goes to ./dist
RUN npm run build

# ── Stage 2: Serve ──
FROM nginx:alpine AS runner
WORKDIR /usr/share/nginx/html

# Copy built static files
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx config for SPA routing
COPY nginx.frontend.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]