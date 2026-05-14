#!/bin/bash
docker build -t forgeai-frontend-sandbox:latest \
  ./docker/frontend-sandbox/
echo "Frontend sandbox image built successfully"
