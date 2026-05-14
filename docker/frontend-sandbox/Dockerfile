FROM node:20-slim

RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    libxrandr2 \
    libxfixes3 \
    libxcomposite1 \
    libxdamage1 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /sandbox

COPY package.json .
COPY vite.config.js .
COPY tailwind.config.js .
COPY postcss.config.js .

RUN npm install
RUN npx playwright install chromium --with-deps

RUN mkdir -p /sandbox/src /sandbox/tests

EXPOSE 5173
