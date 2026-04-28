import logging
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_PAPER,
    BUY_NOTIONAL,
    MAX_DAILY_TRADES,
)

logger = logging.getLogger(__name__)


class AlpacaTrader:
    def __init__(self):
        self.client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
        self.account = self.client.get_account()
        logger.info(f"Connected to Alpaca ({'PAPER' if ALPACA_PAPER else 'LIVE'})")

    def can_trade(self, symbol):
        positions = {p.symbol: int(float(p.qty)) for p in self.client.get_all_positions()}
        if symbol in positions:
            return False
        orders = self.client.get_orders()
        today_buys = len([
            o for o in orders
            if o.side == OrderSide.BUY
            and o.filled_at
            and o.filled_at.date() == datetime.now().date()
        ])
        return today_buys < MAX_DAILY_TRADES

    def buy_stock(self, symbol, notional=None):
        if not self.can_trade(symbol):
            return False
        notional = notional or BUY_NOTIONAL
        try:
            self.client.submit_order(
                MarketOrderRequest(
                    symbol=symbol,
                    notional=notional,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
            )
            logger.info(f"Bought {symbol} ${notional}")
            return True
        except Exception as e:
            logger.error(f"Buy failed for {symbol}: {e}")
            return False
