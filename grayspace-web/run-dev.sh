#!/bin/bash
# Development runner script for GraySpace Web Application

set -e

echo "======================================"
echo "GraySpace Web Application Dev Runner"
echo "======================================"
echo ""

# Check if .env exists in backend
if [ ! -f backend/.env ]; then
    echo "WARNING: backend/.env not found"
    echo "Creating from .env.example..."
    cp backend/.env.example backend/.env
    echo "Please edit backend/.env with your configuration"
    echo ""
fi

# Function to cleanup background processes
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $(jobs -p) 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start backend
echo "Starting backend..."
cd backend
source ../../../.venv/bin/activate 2>/dev/null || python3 -m venv venv && source venv/bin/activate
pip install -q -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
cd ..

# Wait a bit for backend to start
sleep 3

# Start frontend
echo "Starting frontend..."
cd frontend
if [ ! -d "node_modules" ]; then
    echo "Installing frontend dependencies..."
    npm install
fi
npm start &
FRONTEND_PID=$!
cd ..

echo ""
echo "======================================"
echo "Application started!"
echo "======================================"
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3000"
echo "API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers"
echo "======================================"

# Wait for background processes
wait
