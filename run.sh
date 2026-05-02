#!/bin/bash
# Start Drifting Satellite Daemon

# Resolve project path relative to script
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
export PATH="$HOME/.local/bin:$PATH"

cd "$PROJECT_DIR"

echo "Starting Jarvis Backend (Pipecat)..."
uv run src/main.py > /tmp/jarvis_backend.log 2>&1 &
BACKEND_PID=$!

echo "Starting Clap Detector..."
uv run src/clap_detector.py > /tmp/clap_detector.log 2>&1 &
CLAP_PID=$!

# Trap signals for graceful exit
trap "echo 'Stopping all services...'; kill -9 $BACKEND_PID $CLAP_PID; exit 0" SIGINT SIGTERM

echo "Jarvis is now running in the background. (Backend PID: $BACKEND_PID, Clap PID: $CLAP_PID)"

# Wait endlessly
wait
