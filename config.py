import os

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — env vars must be set in the environment

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# ── Phase 1 — Quick Profit ───────────────────────────────────────────────────
# Small position opened on a volume spike with a tight bid/ask spread.
# Auto-closes after 48 hours, +12% gain, or −5% loss.
QUICK_BUY_NOTIONAL = float(os.getenv("QUICK_BUY_NOTIONAL", 300))
QUICK_HOLD_HOURS = int(os.getenv("QUICK_HOLD_HOURS", 48))
QUICK_TAKE_PROFIT_PCT = float(os.getenv("QUICK_TAKE_PROFIT_PCT", 12))
QUICK_STOP_LOSS_PCT = float(os.getenv("QUICK_STOP_LOSS_PCT", 5))
# Minimum ratio of today's daily volume to yesterday's to count as a spike.
VOLUME_SPIKE_MULTIPLIER = float(os.getenv("VOLUME_SPIKE_MULTIPLIER", 2.0))
# Maximum bid/ask spread (as a fraction of mid-price) for a "tight" spread.
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", 0.005))

# ── Phase 2 — Large Profit ───────────────────────────────────────────────────
# Larger position opened when the contract exceeds PHASE2_MATERIALITY_THRESHOLD
# of the awardee's market cap.  Exits via a trailing stop over up to 14 days.
LARGE_BUY_NOTIONAL = float(os.getenv("LARGE_BUY_NOTIONAL", 2000))
# Minimum contract-to-market-cap ratio to trigger the Phase 2 (larger) buy.
PHASE2_MATERIALITY_THRESHOLD = float(os.getenv("PHASE2_MATERIALITY_THRESHOLD", 0.0075))
TRAILING_STOP_DAYS = int(os.getenv("TRAILING_STOP_DAYS", 14))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", 8))
PHASE2_TAKE_PROFIT_PCT = float(os.getenv("PHASE2_TAKE_PROFIT_PCT", 20))

# ── Shared trading config ────────────────────────────────────────────────────
MIN_CONTRACT_AMOUNT = float(os.getenv("MIN_CONTRACT_AMOUNT", 10000000))
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", 30))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", 2))
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "early-gov-contract-watch contact@example.com")
STATE_FILE = os.getenv("STATE_FILE", "state.json")
DAYS_LOOKBACK = int(os.getenv("DAYS_LOOKBACK", 7))
# Maximum number of seen award IDs to retain in state (prevents unbounded growth)
MAX_SEEN_AWARD_IDS = int(os.getenv("MAX_SEEN_AWARD_IDS", 5000))
# Number of days to cache SEC ticker data locally before refreshing
SEC_TICKER_CACHE_DAYS = int(os.getenv("SEC_TICKER_CACHE_DAYS", 7))
# Minimum contract-to-market-cap ratio for a Phase 1 award to be worth trading.
MATERIALITY_THRESHOLD = float(os.getenv("MATERIALITY_THRESHOLD", 0.005))

# ── Backward-compatibility aliases ───────────────────────────────────────────
# Code written before the two-phase engine can keep importing these names.
BUY_NOTIONAL = QUICK_BUY_NOTIONAL
TAKE_PROFIT_PCT = QUICK_TAKE_PROFIT_PCT
STOP_LOSS_PCT = QUICK_STOP_LOSS_PCT
SELL_AFTER_DAYS = int(os.getenv("SELL_AFTER_DAYS", 5))

CONTRACT_AWARD_TYPES = ["A", "B", "C", "D"]
AWARD_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Award Amount",
    "Awarding Agency",
    "Description",
    "Action Date",
    "Modification Number",
]
