#!/bin/bash
# Development startup script

set -e

echo "Starting OpenScrcpy development environment..."

# Check dependencies
command -v python >/dev/null 2>&1 || { echo "Python is required but not installed. Aborting."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Node.js is required but not installed. Aborting."; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required but not installed. Aborting."; exit 1; }

# Create data directory
mkdir -p data

# Backend setup
echo "Setting up backend..."
if [ ! -d "venv" ]; then
    python -m venv venv
fi
source venv/bin/activate 2>/dev/null || source venv/Scripts/activate
pip install -e ".[dev]" --quiet

# Frontend setup
echo "Setting up frontend..."
cd frontend
if [ ! -d "node_modules" ]; then
    npm ci
fi
cd ..

# Initialize database
echo "Initializing database..."
python -c "import asyncio; from app.infrastructure.persistence.sqlite import init_db; asyncio.run(init_db())"

# Start services
echo "Starting services..."
echo "   Backend: http://localhost:8000"
echo "   Frontend: http://localhost:5173"
echo ""

# Start backend in background
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start frontend
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

# Trap signals for cleanup
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

echo "Press Ctrl+C to stop"
wait
