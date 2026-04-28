import logging
from datetime import datetime
from typing import Any, Dict, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_PAPER,
    QUICK_BUY_NOTIONAL,
    MAX_DAILY_TRADES,
)

logger = logging.getLogger(__name__)


class AlpacaTrader:
    def __init__(self):
        self.client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
        self.account = self.client.get_account()
        logger.info(f"Connected to Alpaca ({'PAPER' if ALPACA_PAPER else 'LIVE'})")

        # Market-data client (lazy — initialised on first snapshot/bar call).
        self._data_client = None

    def _get_data_client(self):
        """Return a cached StockHistoricalDataClient, creating it on first use."""
        if self._data_client is None:
            try:
                from alpaca.data import StockHistoricalDataClient  # type: ignore
                self._data_client = StockHistoricalDataClient(
                    ALPACA_API_KEY, ALPACA_SECRET_KEY
                )
            except Exception as e:
                logger.error(f"Could not create data client: {e}")
        return self._data_client

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
        notional = notional or QUICK_BUY_NOTIONAL
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

    def sell_stock(self, symbol: str, qty: Optional[int] = None) -> bool:
        """Liquidate an open position in *symbol*.

        Parameters
        ----------
        symbol:
            Equity ticker to sell.
        qty:
            Number of shares to sell.  When ``None`` (default) the entire
            position is closed via the Alpaca close-position endpoint.
        """
        try:
            if qty is None:
                self.client.close_position(symbol)
            else:
                self.client.submit_order(
                    MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                )
            label = f"qty={qty}" if qty is not None else "full position"
            logger.info(f"Sold {symbol} ({label})")
            return True
        except Exception as e:
            logger.error(f"Sell failed for {symbol}: {e}")
            return False

    def get_snapshot(self, symbol: str) -> Optional[Any]:
        """Return the latest Alpaca market snapshot for *symbol*, or ``None`` on error.

        The returned object exposes:
          ``.latest_trade.price``        — last trade price
          ``.latest_quote.bid_price``    — best bid
          ``.latest_quote.ask_price``    — best ask
          ``.daily_bar.volume``          — today's cumulative volume
          ``.prev_daily_bar.volume``     — yesterday's total volume
        """
        dc = self._get_data_client()
        if dc is None:
            return None
        try:
            from alpaca.data.requests import StockSnapshotRequest  # type: ignore
            req = StockSnapshotRequest(symbol_or_symbols=symbol)
            snaps = dc.get_stock_snapshot(req)
            return snaps.get(symbol) if snaps else None
        except Exception as e:
            logger.error(f"Snapshot failed for {symbol}: {e}")
            return None

    def get_latest_bar(self, symbol: str) -> Optional[Any]:
        """Return the latest 1-minute bar for *symbol*, or ``None`` on error.

        The returned bar exposes ``.volume``, ``.open``, ``.high``,
        ``.low``, ``.close``.
        """
        dc = self._get_data_client()
        if dc is None:
            return None
        try:
            from alpaca.data.requests import StockLatestBarRequest  # type: ignore
            req = StockLatestBarRequest(symbol_or_symbols=symbol)
            bars = dc.get_stock_latest_bar(req)
            return bars.get(symbol) if bars else None
        except Exception as e:
            logger.error(f"Latest bar failed for {symbol}: {e}")
            return None
