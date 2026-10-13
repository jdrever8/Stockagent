"""Daily bar feeds for paper/live trading."""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

from ..config import env


@dataclass
class Bar:
    date: str      # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


class Feed:
    symbols: List[str]

    def dates(self) -> List[str]:
        raise NotImplementedError

    def bars(self, symbol: str, end: str, lookback: int = 30) -> List[Bar]:
        """Bars with date <= end, oldest first (never returns data after ``end``)."""
        raise NotImplementedError

    def bar(self, symbol: str, date: str) -> Optional[Bar]:
        bars = self.bars(symbol, date, 1)
        return bars[-1] if bars and bars[-1].date == date else None


class CSVFeed(Feed):
    """CSV with columns date,symbol,open,high,low,close,volume.

    ``anonymize=True`` renames tickers to S1..Sn and rebases each to 100 so a
    model cannot recall the real price path of a historical period.
    """

    def __init__(self, path: str, anonymize: bool = False):
        rows: Dict[str, List[Bar]] = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.setdefault(r["symbol"], []).append(Bar(
                    r["date"][:10], float(r["open"]), float(r["high"]), float(r["low"]),
                    float(r["close"]), float(r.get("volume") or 0)))
        for v in rows.values():
            v.sort(key=lambda b: b.date)
        self.alias: Dict[str, str] = {}
        if anonymize:
            renamed = {}
            for i, sym in enumerate(sorted(rows), 1):
                k = 100.0 / rows[sym][0].close
                renamed[f"S{i}"] = [Bar(b.date, b.open * k, b.high * k, b.low * k, b.close * k, b.volume)
                                    for b in rows[sym]]
                self.alias[f"S{i}"] = sym
            rows = renamed
        self._bars = rows
        self.symbols = sorted(rows)

    def dates(self) -> List[str]:
        return sorted({b.date for v in self._bars.values() for b in v})

    def bars(self, symbol, end, lookback=30):
        series = [b for b in self._bars.get(symbol, []) if b.date <= end]
        return series[-lookback:]


class AlpacaFeed(Feed):
    """Daily bars from Alpaca market data (free IEX feed by default)."""

    URL = "https://data.alpaca.markets/v2/stocks/bars"

    def __init__(self, symbols: List[str], lookback_days: int = 60, data_feed: str = "iex"):
        self.symbols = list(symbols)
        self.data_feed = data_feed
        start = (dt.date.today() - dt.timedelta(days=lookback_days * 2)).isoformat()
        self._bars = self._fetch(start)

    def _fetch(self, start: str) -> Dict[str, List[Bar]]:
        headers = {"APCA-API-KEY-ID": env("ALPACA_API_KEY_ID"), "APCA-API-SECRET-KEY": env("ALPACA_API_SECRET_KEY")}
        params = {"symbols": ",".join(self.symbols), "timeframe": "1Day", "start": start,
                  "feed": self.data_feed, "limit": 10000, "adjustment": "all"}
        out: Dict[str, List[Bar]] = {s: [] for s in self.symbols}
        while True:
            resp = requests.get(self.URL, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            for sym, bars in (data.get("bars") or {}).items():
                for b in bars:
                    out.setdefault(sym, []).append(Bar(b["t"][:10], b["o"], b["h"], b["l"], b["c"], b.get("v", 0)))
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return out

    def dates(self):
        return sorted({b.date for v in self._bars.values() for b in v})

    def bars(self, symbol, end, lookback=30):
        return [b for b in self._bars.get(symbol, []) if b.date <= end][-lookback:]
