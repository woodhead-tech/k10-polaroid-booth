#!/bin/bash
set -e
cd "$(dirname "$0")"

mkdir -p photos

# Get local IP (cross-platform)
if command -v hostname >/dev/null 2>&1 && hostname -I >/dev/null 2>&1; then
    LOCAL_IP=$(hostname -I | awk '{print $1}')
elif command -v ipconfig >/dev/null 2>&1; then
    LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "localhost")
else
    LOCAL_IP=$(ifconfig 2>/dev/null | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
fi

echo ""
echo "=== POLAROID PHOTO BOOTH ==="
echo "Server IP:    ${LOCAL_IP}"
echo "Gallery URL:  http://${LOCAL_IP}:8080/booth.html"
echo "Upload URL:   http://${LOCAL_IP}:8080/upload"
echo "Style status: http://${LOCAL_IP}:8080/api/style"
echo "============================="
echo ""
echo "Point each K10's SERVER_URL at: http://${LOCAL_IP}:8080"
echo ""

# Set up venv if needed
if [ ! -d .venv ]; then
    echo "Setting up virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
fi

.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080
