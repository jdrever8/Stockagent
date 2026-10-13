"""Interactive Brokers adapter (TWS / IB Gateway socket API via ``ib_async``).

Paper and live are separate IBKR logins. Paper account IDs start with "D"
(e.g. DU1234567); live ones don't. The adapter checks the connected account
against the mode it was asked for, so a paper run can never reach a live
account by pointing at the wrong port.

Default ports: IB Gateway paper 4002 / live 4001, TWS paper 7497 / live 7496.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

from ..config import env
from .feeds import Bar, Feed

PAPER_PORT, LIVE_PORT = 4002, 4001


def _ib_async():
    try:
        import ib_async
    except ImportError as exc:  # optional dependency
        raise RuntimeError("pip install ib_async  (needed for --broker ibkr / --feed ibkr)") from exc
    return ib_async


def connect(live: bool, client_id: Optional[int] = None, readonly: bool = False):
    ib_async = _ib_async()
    ib = ib_async.IB()
    port = int(env("IBKR_PORT", str(LIVE_PORT if live else PAPER_PORT)))
    ib.connect(env("IBKR_HOST", "127.0.0.1"), port, clientId=client_id or int(env("IBKR_CLIENT_ID", "17")),
               readonly=readonly, timeout=15)
    return ib


def is_paper_account(account_id: str) -> bool:
    return account_id.upper().startswith("D")


class IBKRBroker:
    """Same interface as AlpacaBroker: account(), positions(), cancel_all(), submit_limit()."""

    ORDER_PREFIX = "sa-"

    def __init__(self, live: bool = False, account: Optional[str] = None, ib=None, currency: str = "USD"):
        self.live = live
        self.currency = env("IBKR_CURRENCY", currency)
        self.ib = ib or connect(live)
        accounts = self.ib.managedAccounts()
        self.account_id = account or env("IBKR_ACCOUNT") or (accounts[0] if len(accounts) == 1 else "")
        if not self.account_id:
            raise RuntimeError(f"several accounts {accounts}; set IBKR_ACCOUNT to the dedicated one")
        if self.account_id not in accounts:
            raise RuntimeError(f"account {self.account_id} is not available on this connection ({accounts})")
        if live and is_paper_account(self.account_id):
            raise PermissionError(f"{self.account_id} is a paper account but --real-money was given")
        if not live and not is_paper_account(self.account_id):
            raise PermissionError(f"{self.account_id} looks like a LIVE account; paper mode refuses to trade it")

    def _value(self, tag: str) -> float:
        rows = [v for v in self.ib.accountSummary(self.account_id) if v.tag == tag]
        preferred = [v for v in rows if v.currency == self.currency] or [v for v in rows if v.currency == "BASE"] or rows
        if not preferred:
            raise RuntimeError(f"account summary has no {tag}")
        return float(preferred[0].value)

    def account(self) -> dict:
        return {"cash": self._value("TotalCashValue"), "equity": self._value("NetLiquidation"),
                "buying_power": self._value("AvailableFunds"), "trading_blocked": False}

    def positions(self) -> Dict[str, int]:
        return {p.contract.symbol: int(p.position) for p in self.ib.positions(self.account_id)
                if p.contract.secType == "STK" and p.position}

    def cancel_all(self) -> None:
        """Cancel only this program's open orders (orderRef prefix), never manual ones."""
        for trade in self.ib.openTrades():
            if trade.order.account == self.account_id and (trade.order.orderRef or "").startswith(self.ORDER_PREFIX):
                self.ib.cancelOrder(trade.order)

    def submit_limit(self, symbol: str, side: str, qty: int, limit: float, client_id: str) -> dict:
        ib_async = _ib_async()
        contract = ib_async.Stock(symbol, "SMART", self.currency)
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"IBKR does not recognise {symbol}")
        order = ib_async.LimitOrder(side.upper(), qty, round(limit, 2), tif="DAY", account=self.account_id,
                                    orderRef=client_id if client_id.startswith(self.ORDER_PREFIX)
                                    else self.ORDER_PREFIX + client_id, outsideRth=False)
        trade = self.ib.placeOrder(qualified[0], order)
        self.ib.sleep(1)
        return {"id": trade.order.orderId, "status": trade.orderStatus.status}

    def close(self) -> None:
        self.ib.disconnect()


class IBKRFeed(Feed):
    """Daily bars from IBKR historical data (needs US equity market data subscriptions;
    the paper login shares the live account's subscriptions)."""

    def __init__(self, symbols: List[str], ib=None, duration: str = "6 M", live: bool = False):
        ib_async = _ib_async()
        self.symbols = list(symbols)
        own = ib is None
        ib = ib or connect(live, client_id=int(env("IBKR_CLIENT_ID", "17")) + 1, readonly=True)
        self._bars: Dict[str, List[Bar]] = {}
        try:
            for sym in self.symbols:
                contract = ib.qualifyContracts(ib_async.Stock(sym, "SMART", env("IBKR_CURRENCY", "USD")))
                if not contract:
                    raise RuntimeError(f"IBKR does not recognise {sym}")
                bars = ib.reqHistoricalData(contract[0], endDateTime="", durationStr=duration,
                                            barSizeSetting="1 day", whatToShow="TRADES", useRTH=True)
                if not bars:
                    raise RuntimeError(f"no historical data for {sym}: check market data subscriptions")
                self._bars[sym] = [Bar(_date(b.date), b.open, b.high, b.low, b.close, b.volume) for b in bars]
        finally:
            if own:
                ib.disconnect()

    def dates(self):
        return sorted({b.date for v in self._bars.values() for b in v})

    def bars(self, symbol, end, lookback=30):
        return [b for b in self._bars.get(symbol, []) if b.date <= end][-lookback:]


def _date(d) -> str:
    if isinstance(d, (dt.date, dt.datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]
