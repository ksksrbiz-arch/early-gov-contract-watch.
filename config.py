import os
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
BUY_NOTIONAL = float(os.getenv("BUY_NOTIONAL", 300))
MIN_CONTRACT_AMOUNT = float(os.getenv("MIN_CONTRACT_AMOUNT", 10000000))
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", 30))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", 2))
# Sell logic configuration (reserved for future implementation)
SELL_AFTER_DAYS = int(os.getenv("SELL_AFTER_DAYS", 5))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", 12))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", 6))
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "early-gov-contract-watch contact@example.com")
STATE_FILE = os.getenv("STATE_FILE", "state.json")
DAYS_LOOKBACK = int(os.getenv("DAYS_LOOKBACK", 7))
# Maximum number of seen award IDs to retain in state (prevents unbounded growth)
MAX_SEEN_AWARD_IDS = int(os.getenv("MAX_SEEN_AWARD_IDS", 5000))
# Number of days to cache SEC ticker data locally before refreshing
SEC_TICKER_CACHE_DAYS = int(os.getenv("SEC_TICKER_CACHE_DAYS", 7))
# Minimum contract-to-market-cap ratio to consider an award material
MATERIALITY_THRESHOLD = float(os.getenv("MATERIALITY_THRESHOLD", 0.005))

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
