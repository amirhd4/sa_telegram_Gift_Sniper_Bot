# Telegram Stars Gift Sniper & Floor Analyzer Bot

High-performance, event-driven Telegram Stars Gift Sniper & Floor Price Analyzer bot built with Python 3.12+, async I/O (`asyncio` + `uvloop`), native MTProto API (`Telethon`), and hot memory cache.

## 🚀 Key Features

1. **High Performance & Sub-5ms Reference Floor Engine**:
   - Computes reference floor price $F_{ref} = \max(F_{general}, F_{model}, F_{background})$ in $<5\mu s$.
   - Special pricing evaluation for rare models (e.g., *Bank Vault*, *Star Pupil*) and premium backgrounds (*Black* & *Onyx Black*).
   - Trimmed Moving Average algorithm to eliminate wash trading manipulation and price outliers.

2. **In-Memory Hot Cache**:
   - In-memory data store for $O(1)$ instant lookups without database roundtrip bottlenecks during market scanning.

3. **Isolated MTProto Session Architecture**:
   - Separates **Scanner Session** from **Buyer Session** to avoid rate limiting and `FLOOD_WAIT` bans.
   - Dynamic anti-floodwait handler for robust MTProto request retries.

4. **Telegram Stars Auto-Buyer Engine**:
   - Executes automatic purchases via `payments.getPaymentForm`, `inputInvoiceStarGiftResale` / `inputInvoiceStarGift`, and `payments.sendStarsForm`.
   - Budget safety guards (`MAX_STARS_PER_GIFT`, `DAILY_STARS_BUDGET`).
   - Buyer privacy support (`HIDE_NAME=true`).

5. **Real-time Channel Deal Alerts**:
   - Instant deal alerts sent to Telegram channels with gift metadata, model, background, listed price, floor price, discount percentage, and direct purchase links.

---

## 🛠 Project Structure

```
├── main.py              # Application entrypoint & CLI orchestrator
├── config.py            # Environment configuration via Pydantic
├── database.py          # Async SQLite database schema & repository
├── floor_engine.py      # Hot cache & reference floor computation engine
├── scanner.py           # MTProto market scanner & deal evaluator
├── auto_buyer.py        # Telegram Stars auto-buyer engine
├── anti_flood.py        # MTProto Anti-FloodWait rate limit handler
├── alert_bot.py         # Telegram channel alert logger bot
├── .env.example         # Template for environment variables
├── requirements.txt     # Python dependencies
└── tests/               # Test suite
```

---

## 💻 Setup & Running

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your Telegram API details:
```bash
cp .env.example .env
```

### 3. Initialize Database & Seed Example Data
```bash
python main.py --init-db --seed
```

### 4. Run Sniper & Analyzer Bot
```bash
python main.py --mode all
```

---

## 🧪 Testing

Run test suite:
```bash
PYTHONPATH=. pytest -v
```
