#!/bin/bash
# ================================================================================
# DEPLOYMENT STRATEGY FOR BINANCE RESEARCH BOT
# ================================================================================

# ==============================================================================
# FOR VERCEL FRONTEND DEPLOYMENT
# ==============================================================================
# WHAT TO UPLOAD:
# - Only frontend/ folder
# - frontend/.env.production (if needed)
#
# ENVIRONMENT VARIABLES IN VERCEL DASHBOARD:
# - NEXT_PUBLIC_BACKEND_CONSERVATIVE_URL=http://localhost:5001
# - NEXT_PUBLIC_BACKEND_AGGRESSIVE_URL=http://localhost:5002
#
# Frontend needs NO other .env - it's static and connects to backend via HTTP

# ==============================================================================
# FOR BACKEND DEPLOYMENT
# ==============================================================================

# OPTION 1: DOCKER-COMPOSE (EASIEST - Recommended)
# - Upload: backend/docker-compose.yml
# - docker-compose automatically starts BOTH profiles in separate containers
# - Each container gets its own .env via environment section in docker-compose.yml

# OPTION 2: SEPARATE VPS INSTANCES (PRODUCTION)
# Conservative Profile on VPS-1:
#   - Upload: backend/.env + backend/profiles/conservative.env
#   - Run: python run_multiprofile.py

# Aggressive Profile on VPS-2:
#   - Upload: backend/.env + backend/profiles/aggressive.env
#   - Run: python run_multiprofile.py

# OPTION 3: SINGLE INSTANCE (SIMPLE)
# - Upload: backend/.env + backend/profiles/{conservative,aggressive}.env
# - Manually switch which profile to run

# ==============================================================================
# CURRENT FOLDER STRUCTURE FOR DEPLOYMENT
# ==============================================================================
# backend/
#   ├── .env (MAIN - shared by all profiles)
#   ├── profiles/
#   │   ├── conservative.env (CONSERVATIVE OVERRIDES)
#   │   └── aggressive.env (AGGRESSIVE OVERRIDES)
#   └── [bot code]
#
# frontend/
#   └── .env.production (optional - for Vercel)

# ==============================================================================
# WHAT EACH FILE DOES
# ==============================================================================

# backend/.env
#   - Shared configuration for ALL profiles
#   - API keys, base settings, strategy modules
#   - UPLOADED TO: backend server

# backend/profiles/conservative.env
#   - Overrides for Conservative profile ONLY
#   - Lower risk, stricter filters
#   - UPLOADED TO: backend server (if running conservative profile)

# backend/profiles/aggressive.env
#   - Overrides for Aggressive profile ONLY
#   - Higher risk, looser filters, leverage enabled
#   - UPLOADED TO: backend server (if running aggressive profile)

# frontend/.env.production
#   - Frontend configuration (optional)
#   - Backend URLs for production
#   - UPLOADED TO: Vercel (via environment variables is better)

# ==============================================================================
# GITIGNORE STRATEGY
# ==============================================================================
# Add to .gitignore:
# backend/.env (don't commit with keys)
# backend/profiles/*.env (don't commit with keys)
# frontend/.env.production (don't commit)
#
# Instead: Store in GitHub Secrets or 1Password, add during CI/CD

# ==============================================================================
# .gitignore EXAMPLE
# ==============================================================================
# # Environment files with secrets
# backend/.env
# backend/.env.local
# backend/profiles/*.env
# frontend/.env.production
# frontend/.env.local
#
# # But DO track the examples/templates:
# !backend/.env.example
# !backend/profiles/*.env.example
