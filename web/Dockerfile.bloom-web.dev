
# ------------------------
# Bloom Web DEV Dockerfile
# ------------------------
FROM node:20-bookworm

# Set working directory to root of monorepo
WORKDIR /app

COPY package*.json ./
COPY web/package*.json ./web/
COPY packages/*/package*.json ./packages/

ENV NODE_ENV=development
ENV NEXT_PUBLIC_SUPABASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIiwiYXVkIjoiYXV0aGVudGljYXRlZCIsImlhdCI6MTc2MDQwNzUwMSwiZXhwIjoyMDc1OTgzNTAxfQ.BNyTbLii0WFXxlLk8JMVzCfRzmPqCYCYW5FtC1ErZZE
ENV SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiaWF0IjoxNzYwNDA3NTYzLCJleHAiOjIwNzU5ODM1NjN9.MQtGFnfpIKzWTvUIDTH7IUyym8TXDW_kjcWcl-_LNgA

RUN npm install --workspaces

COPY web/ ./
COPY packages ./packages

WORKDIR /app/web

EXPOSE 3000
CMD ["npm", "run", "dev"]
