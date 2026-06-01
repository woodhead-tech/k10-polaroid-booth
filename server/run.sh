#!/bin/bash
set -e
cd "$(dirname "$0")"

mkdir -p photos

echo ""
echo "=== GRADUATION PHOTO BOOTH ==="
echo "Server IP(s): $(hostname -I | tr ' ' '\n' | grep -v '^$' | head -3)"
echo "Gallery URL:  http://$(hostname -I | awk '{print $1}'):8080/booth.html"
echo "Upload URL:   http://$(hostname -I | awk '{print $1}'):8080/upload"
echo "==============================="
echo ""

uvicorn server:app --host 0.0.0.0 --port 8080
