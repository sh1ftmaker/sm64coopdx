#!/bin/bash
# Build sm64coopdx for Emscripten/WebAssembly
# Usage: ./build_web.sh         (incremental build)
#        ./build_web.sh clean   (clean then build)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Source Emscripten SDK
if [ -f "$HOME/emsdk/emsdk_env.sh" ]; then
    source "$HOME/emsdk/emsdk_env.sh" 2>/dev/null
elif [ -f "/usr/local/emsdk/emsdk_env.sh" ]; then
    source "/usr/local/emsdk/emsdk_env.sh" 2>/dev/null
fi

if ! command -v emcc &>/dev/null; then
    echo "ERROR: emcc not found. Install Emscripten SDK first."
    exit 1
fi

# Only clean if explicitly requested
if [ "$1" = "clean" ]; then
    echo "Cleaning build/us_pc..."
    rm -rf build/us_pc
fi

echo "Building sm64coopdx for web (TARGET_WEB=1)..."
emmake make TARGET_WEB=1 VERSION=us DEBUG=1 -j$(nproc) 2>&1

# Kill old HTTP server and restart with fresh build
echo ""
echo "Restarting HTTP server on port 8083..."
fuser -k 8083/tcp 2>/dev/null || true
sleep 0.5
cd "$SCRIPT_DIR/build/us_pc"
python3 -m http.server 8083 --bind 0.0.0.0 &
SERVER_PID=$!
echo "  Server PID: $SERVER_PID"
echo "  Game: http://localhost:8083/sm64coopdx.html"
echo "  (also accessible on LAN via your IP:8083)"
