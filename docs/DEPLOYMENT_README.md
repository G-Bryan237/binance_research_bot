# DEPLOYMENT GUIDE - BINANCE RESEARCH BOT

## 📋 Quick Answer to Your Questions

### 1. **Refresh Error & Styling**
Dev server is running without errors. If you see styling issues:
- Clear browser cache: `Ctrl+Shift+Delete`
- Restart dev server: `npm run dev`
- Check browser console for CSS loading errors

### 2. **Which .env to Upload for Deployment?**

| Where | What | Why |
|-------|------|-----|
| **Vercel (Frontend)** | Nothing! OR set env vars in dashboard | Frontend is static JavaScript, connects via HTTP |
| **Backend Server** | `backend/.env` + profile `.env` | Profiles override risk settings |
| **GitHub** | `.env.example` only | Never commit real keys |

---

## 📂 File Structure & What Gets Uploaded

```
binance_research_bot/
├── backend/
│   ├── .env                          ← UPLOAD (no secrets!)
│   ├── .env.example                  ← COMMIT to Git (template)
│   ├── profiles/
│   │   ├── conservative.env          ← UPLOAD (no secrets!)
│   │   ├── aggressive.env            ← UPLOAD (no secrets!)
│   │   ├── .env.conservative.example ← COMMIT to Git (template)
│   │   └── .env.aggressive.example   ← COMMIT to Git (template)
│   └── [bot code]
├── frontend/
│   ├── .env.production (optional)    ← UPLOAD via Vercel dashboard
│   └── [Next.js code]
└── .gitignore                        ← Prevents committing secrets
```

---

## 🚀 HOW TO DEPLOY

### **Option 1: Vercel Frontend + Separate Backend Servers (RECOMMENDED)**

#### Frontend → Vercel:
```bash
# What to upload to Vercel
frontend/
├── app/
├── components/
├── lib/
├── package.json
└── ... (all frontend files)

# Vercel Environment Variables (in Vercel Dashboard):
NEXT_PUBLIC_BACKEND_CONSERVATIVE_URL=https://conservative-bot.example.com
NEXT_PUBLIC_BACKEND_AGGRESSIVE_URL=https://aggressive-bot.example.com
```

#### Backend → VPS/Server 1 (Conservative):
```bash
# Upload
backend/.env                    # API keys + main config
backend/profiles/conservative.env  # Conservative overrides
# Run: python run_multiprofile.py
```

#### Backend → VPS/Server 2 (Aggressive):
```bash
# Upload
backend/.env                    # API keys + main config
backend/profiles/aggressive.env    # Aggressive overrides
# Run: python run_multiprofile.py
```

---

### **Option 2: Docker with docker-compose (Easiest)**

```bash
# Upload to server:
docker-compose.yml
backend/.env
backend/profiles/conservative.env
backend/profiles/aggressive.env

# Run:
docker-compose up -d
# Automatically starts both profiles on ports 5001 & 5002
```

---

## 🔐 How to Handle Secrets

### ❌ **DON'T DO THIS:**
```bash
git add backend/.env
git commit -m "add env"
git push  # 🚨 EXPOSED!
```

### ✅ **DO THIS INSTEAD:**

#### Using GitHub Secrets + GitHub Actions:
```yaml
# .github/workflows/deploy.yml
env:
  BINANCE_SPOT_API_KEY: ${{ secrets.BINANCE_SPOT_API_KEY }}
  BINANCE_SPOT_API_SECRET: ${{ secrets.BINANCE_SPOT_API_SECRET }}
```

#### Using Vercel Secrets:
1. Go to Vercel Dashboard → Project → Settings → Environment Variables
2. Add: `NEXT_PUBLIC_BACKEND_CONSERVATIVE_URL`
3. Deploy - Vercel injects them automatically

#### Using Docker Secrets:
```bash
docker-compose.yml:
  environment:
    BINANCE_SPOT_API_KEY: ${BINANCE_SPOT_API_KEY}
    BINANCE_SPOT_API_SECRET: ${BINANCE_SPOT_API_SECRET}

# Run with:
export BINANCE_SPOT_API_KEY=xxx
export BINANCE_SPOT_API_SECRET=xxx
docker-compose up -d
```

---

## 🔄 Profile-Specific Configuration

### **Main .env (Shared)**
```
BOT_ALLOWED_SYMBOLS=BTCUSDT,ETHUSDT,XRPUSDT,SOLUSDT,BNBUSDT
BOT_ENABLE_M1=true
BOT_ENABLE_M4=true
BOT_ENABLE_M5=true
BOT_ENABLE_M6=true
BOT_RISK_PER_TRADE_PCT=0.0075          # DEFAULT
```

### **conservative.env (Overrides)**
```
BOT_PROFILE_ID=conservative
BOT_DASHBOARD_PORT=5001
BOT_RISK_PER_TRADE_PCT=0.005           # OVERRIDE: Lower risk
BOT_MAX_DAILY_LOSS_PCT=0.015           # OVERRIDE: Tighter stop loss
BOT_ENABLE_LEVERAGE_2X=false           # OVERRIDE: No leverage
```

### **aggressive.env (Overrides)**
```
BOT_PROFILE_ID=aggressive
BOT_DASHBOARD_PORT=5002
BOT_RISK_PER_TRADE_PCT=0.012           # OVERRIDE: Higher risk
BOT_MAX_DAILY_LOSS_PCT=0.04            # OVERRIDE: Looser stop loss
BOT_ENABLE_LEVERAGE_2X=true            # OVERRIDE: Leverage enabled
```

**KEY POINT:** All strategy modules (M1-M6) are enabled in BOTH profiles. Only RISK parameters differ.

---

## 📋 Deployment Checklist

### Before Uploading:

- [ ] API keys are in `.env` (not `.env.example`)
- [ ] `backend/.env` is in `.gitignore`
- [ ] `backend/profiles/*.env` are in `.gitignore`
- [ ] `.env.example` IS committed to Git (no secrets)
- [ ] Frontend `.env.production` is not committed
- [ ] Build passes: `npm run build` (frontend), `python -m pytest` (backend)

### Before Running:

- [ ] `backend/.env` has real Binance API keys
- [ ] `backend/profiles/conservative.env` exists (or is symlinked)
- [ ] `backend/profiles/aggressive.env` exists (or is symlinked)
- [ ] Ports 5001, 5002 are open on backend server
- [ ] Frontend can reach backend URLs

### After Deployment:

- [ ] Frontend loads at Vercel URL
- [ ] Profile dropdown works
- [ ] Can switch between Conservative/Aggressive
- [ ] Trades appear in dashboard
- [ ] "All Profiles" view combines both

---

## 💡 Pro Tips

### Tip 1: Use `envsubst` to generate .env from template
```bash
# On production server:
envsubst < backend/.env.example > backend/.env
# Fills in $BINANCE_SPOT_API_KEY etc from system env vars
```

### Tip 2: Use `.env.local` for local overrides
```bash
# Development (git ignored):
backend/.env           # checked into git
backend/.env.local     # local overrides only

# Read order: .env → .env.local → environment variables
```

### Tip 3: Validate .env before running
```bash
# Check all required keys are present
python -c "from bot.config import get_config; cfg = get_config(); print('OK')"
```

---

## ⚠️ Common Mistakes

| Mistake | Impact | Fix |
|---------|--------|-----|
| Committing `.env` with keys | 🚨 SECURITY BREACH | Add to `.gitignore`, rotate keys immediately |
| Only uploading `.env`, not profiles | ❌ Profiles don't differentiate | Upload both profile `.env` files |
| Same ports for both profiles | ❌ Port conflict | Use 5001 & 5002 in respective profiles |
| Frontend has `process.env.API_KEY` | ❌ Exposes secrets in browser | Only use `NEXT_PUBLIC_*` for frontend URLs |
| Not updating `.gitignore` | ⚠️ Accidental key commits | Update now using provided .gitignore |

---

## 📞 Questions?

- **Frontend styling fails after refresh?** → Clear cache + restart dev server
- **Can't distinguish profiles?** → Each profile has own port (5001, 5002) + `.env` file
- **How to hide secrets in Git?** → Add to `.gitignore`, use GitHub Secrets or CI/CD
- **Upload main .env to Vercel?** → ❌ No! Frontend doesn't need it. Use Vercel dashboard for backend URLs.
