.PHONY: help install dev build preview typecheck clean up down docker-build refresh restart

# Frontend directory
FRONTEND_DIR := src/frontend

# Docker compose file
COMPOSE_FILE := docker-compose.dev.yml

# Default target
help:
	@echo "Available targets:"
	@echo ""
	@echo "Frontend:"
	@echo "  install    - Install frontend dependencies"
	@echo "  dev        - Start development server"
	@echo "  build      - Build frontend for production"
	@echo "  preview    - Preview production build"
	@echo "  typecheck  - Run TypeScript type checking"
	@echo "  clean      - Remove node_modules and build artifacts"
	@echo ""
	@echo "Backend (Docker):"
	@echo "  up         - Start backend services"
	@echo "  down       - Stop backend services"
	@echo "  restart    - Restart backend services (no rebuild)"
	@echo "  docker-build - Build Docker image"
	@echo "  refresh    - Rebuild and restart backend services"

# Install dependencies
install:
	@echo "Installing frontend dependencies..."
	cd $(FRONTEND_DIR) && npm install

# Start development server
dev:
	@echo "Starting development server..."
	cd $(FRONTEND_DIR) && npm run dev

# Build for production
build:
	@echo "Building frontend for production..."
	cd $(FRONTEND_DIR) && npm run build

# Preview production build
preview:
	@echo "Previewing production build..."
	cd $(FRONTEND_DIR) && npm run preview

# Type checking
typecheck:
	@echo "Running TypeScript type checking..."
	cd $(FRONTEND_DIR) && npm run typecheck

# Clean build artifacts and dependencies
clean:
	@echo "Cleaning build artifacts and dependencies..."
	rm -rf $(FRONTEND_DIR)/node_modules
	rm -rf $(FRONTEND_DIR)/dist

# Start backend services
up:
	@echo "Starting backend services..."
	docker compose -f $(COMPOSE_FILE) up -d

# Stop backend services
down:
	@echo "Stopping backend services..."
	docker compose -f $(COMPOSE_FILE) down

# Build Docker image
docker-build:
	@echo "Building Docker image..."
	docker compose -f $(COMPOSE_FILE) build

# Restart backend services (no rebuild)
restart:
	@echo "Restarting backend services..."
	docker compose -f $(COMPOSE_FILE) restart

# Rebuild and restart backend services
refresh:
	@echo "Rebuilding and restarting backend services..."
	docker compose -f $(COMPOSE_FILE) down
	docker compose -f $(COMPOSE_FILE) build
	docker compose -f $(COMPOSE_FILE) up -d
