"""Agent paper trade: a real-money-style ledger filled at live market prices, with no broker.

The ledger is one JSON file (committed to the repo so it survives ephemeral
machines). Values are kept in a base currency (GBP by default); US stocks are
converted at the live GBP/USD rate, and London prices quoted in pence (GBX) are
converted to pounds.

No trading rules: any symbol, any size, short selling allowed. The only
constraint is the budget: a buy cannot spend more cash than the book holds.

Symbols: ``AAPL`` (US), ``VOD.L`` (London). Quotes come from Stooq, then Yahoo,
neither of which needs a key. If neither is reachable a price can be entered by
hand with ``--price`` and a mandatory ``--source`` so the record says where it
came from.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests

DEFAULT_PATH = os.path.join("paper", "agent_book.json")
UA = {"User-Agent": "Mozilla/5.0 (stockagent paper book)"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
@dataclass
class Quote:
    symbol: str
    price: float          # in ``currency`` units (pounds, not pence, for London)
    currency: str
    time: str
    source: str


def market_of(symbol: str) -> Tuple[str, str]:
    """Return (stooq symbol, quote currency) for a canonical symbol."""
    sym = symbol.upper()
    if sym.endswith(".L"):
        return sym[:-2].lower() + ".uk", "GBX"
    return sym.lower() + ".us", "USD"


def yahoo_symbol(symbol: str) -> str:
    return symbol.upper()


def _stooq(symbols: List[str]) -> Dict[str, Tuple[float, str]]:
    codes = {("gbpusd" if s == "GBPUSD" else market_of(s)[0]).upper(): s for s in symbols}
    url = "https://stooq.com/q/l/?s=" + "+".join(c.lower() for c in codes) + "&f=sd2t2ohlcv&h&e=csv"
    resp = requests.get(url, headers=UA, timeout=20)
    resp.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(resp.text)):
        sym = codes.get((row.get("Symbol") or "").upper())
        close = row.get("Close", "N/D")
        if sym and close not in ("N/D", "", None):
            out[sym] = (float(close), f"{row.get('Date')}T{row.get('Time')}")
    return out


def _yahoo(symbol: str) -> Tuple[float, str]:
    ysym = "GBPUSD=X" if symbol == "GBPUSD" else yahoo_symbol(symbol)
    resp = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}",
                        params={"range": "1d", "interval": "1m"}, headers=UA, timeout=20)
    resp.raise_for_status()
    meta = resp.json()["chart"]["result"][0]["meta"]
    ts = dt.datetime.fromtimestamp(meta["regularMarketTime"], dt.timezone.utc).isoformat()
    return float(meta["regularMarketPrice"]), ts


def fetch_quotes(symbols: List[str]) -> Dict[str, Quote]:
    """Live quotes; London prices converted from pence to pounds. Missing symbols are omitted."""
    symbols = [s.upper() for s in symbols]
    raw: Dict[str, Tuple[float, str, str]] = {}
    try:
        for s, (p, t) in _stooq(symbols).items():
            raw[s] = (p, t, "stooq")
    except requests.RequestException:
        pass
    for s in symbols:
        if s not in raw:
            try:
                p, t = _yahoo(s)
                raw[s] = (p, t, "yahoo")
            except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
                pass
    out = {}
    for s, (p, t, src) in raw.items():
        if s == "GBPUSD":
            out[s] = Quote(s, p, "USD", t, src)
            continue
        ccy = market_of(s)[1]
        if ccy == "GBX":
            p, ccy = p / 100.0, "GBP"
        out[s] = Quote(s, p, ccy, t, src)
    return out


def currency_of(symbol: str) -> str:
    return "GBP" if market_of(symbol)[1] == "GBX" else "USD"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
class Book:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        if not os.path.exists(path):
            raise FileNotFoundError(f"no paper book at {path}; run `python main.py book init`")
        with open(path, encoding="utf-8") as fh:
            self.data = json.load(fh)

    @staticmethod
    def init(path: str, capital: float, base: str = "GBP", fee: float = 0.0, owner: str = "agent",
             force: bool = False) -> "Book":
        if os.path.exists(path) and not force:
            raise FileExistsError(f"{path} exists; pass --force to start over")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {"owner": owner, "base": base, "start_capital": capital, "created": now(), "cash": capital,
                "fee_per_trade": fee, "positions": {}, "trades": [], "marks": [],
                "rules": "none: any symbol, any size, shorting allowed; buys limited to available cash"}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        return Book(path)

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1)
        os.replace(tmp, self.path)

    # --- conversion -------------------------------------------------------
    def fx_to_base(self, currency: str, quotes: Optional[Dict[str, Quote]] = None,
                   manual_fx: Optional[float] = None) -> Tuple[float, str]:
        """Units of base currency per 1 unit of ``currency``."""
        base = self.data["base"]
        if currency == base:
            return 1.0, "identity"
        if manual_fx:
            return manual_fx, "manual"
        if {currency, base} == {"USD", "GBP"}:
            q = (quotes or {}).get("GBPUSD") or fetch_quotes(["GBPUSD"]).get("GBPUSD")
            if not q:
                raise RuntimeError("no live GBP/USD rate; pass --fx (GBP per USD) with --source")
            rate = 1.0 / q.price if base == "GBP" else q.price
            return rate, q.source
        raise RuntimeError(f"no conversion from {currency} to {base}")

    # --- trading ----------------------------------------------------------
    def trade(self, side: str, symbol: str, qty: float, price: Optional[float] = None,
              source: Optional[str] = None, fx: Optional[float] = None, note: str = "") -> dict:
        side, symbol = side.lower(), symbol.upper()
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if qty <= 0:
            raise ValueError("quantity must be positive")
        currency = currency_of(symbol)
        if price is None:
            quote = fetch_quotes([symbol]).get(symbol)
            if not quote:
                raise RuntimeError(f"no live quote for {symbol}; pass --price and --source")
            price, src, qtime = quote.price, quote.source, quote.time
        else:
            if not source:
                raise ValueError("a manual --price needs --source saying where the price came from")
            src, qtime = f"manual: {source}", now()
        rate, fx_src = self.fx_to_base(currency, manual_fx=fx)
        value = qty * price * rate
        fee = float(self.data.get("fee_per_trade", 0.0))
        d = self.data
        if side == "buy" and value + fee > d["cash"] + 1e-9:
            max_qty = max(0.0, (d["cash"] - fee) / (price * rate))
            raise ValueError(f"buy needs {value + fee:,.2f} {d['base']} but cash is {d['cash']:,.2f}; "
                             f"max quantity {max_qty:.4f}")
        pos = d["positions"].setdefault(symbol, {"qty": 0.0, "cost_base": 0.0, "currency": currency})
        signed = qty if side == "buy" else -qty
        # Average cost on the side being added to; realised P&L on the side being reduced.
        realised = 0.0
        if pos["qty"] == 0 or (pos["qty"] > 0) == (signed > 0):
            pos["cost_base"] += abs(signed) * price * rate
        else:
            closing = min(abs(signed), abs(pos["qty"]))
            avg = pos["cost_base"] / abs(pos["qty"])
            direction = 1 if pos["qty"] > 0 else -1
            realised = direction * closing * (price * rate - avg)
            pos["cost_base"] -= closing * avg
            opening = abs(signed) - closing
            if opening > 0:   # flipped through zero
                pos["cost_base"] = opening * price * rate
        pos["qty"] = round(pos["qty"] + signed, 8)
        if pos["qty"] == 0:
            pos["cost_base"] = 0.0
        d["cash"] += -value - fee if side == "buy" else value - fee
        entry = {"time": now(), "side": side, "symbol": symbol, "qty": qty, "price": price, "currency": currency,
                 "fx_to_base": round(rate, 6), "value_base": round(value, 2), "fee": fee,
                 "realised_pnl": round(realised, 2), "price_source": src, "quote_time": qtime,
                 "fx_source": fx_src, "note": note}
        d["trades"].append(entry)
        if pos["qty"] == 0:
            del d["positions"][symbol]
        self.save()
        return entry

    # --- valuation --------------------------------------------------------
    def mark(self, record: bool = True, manual_prices: Optional[Dict[str, float]] = None) -> dict:
        d = self.data
        symbols = list(d["positions"])
        quotes = fetch_quotes(symbols + ["GBPUSD"]) if symbols else {}
        rows, total, missing = [], d["cash"], []
        for sym, pos in d["positions"].items():
            q = quotes.get(sym)
            price = (manual_prices or {}).get(sym) or (q.price if q else None)
            if price is None:
                missing.append(sym)
                continue
            rate, _ = self.fx_to_base(pos["currency"], quotes)
            value = pos["qty"] * price * rate
            total += value
            rows.append({"symbol": sym, "qty": pos["qty"], "price": price, "currency": pos["currency"],
                         "value_base": round(value, 2),
                         "unrealised_pnl": round(value - (pos["cost_base"] if pos["qty"] > 0 else -pos["cost_base"]), 2)})
        result = {"time": now(), "base": d["base"], "cash": round(d["cash"], 2), "equity": round(total, 2),
                  "return": total / d["start_capital"] - 1, "positions": rows, "unpriced": missing,
                  "realised_pnl": round(sum(t.get("realised_pnl", 0) for t in d["trades"]), 2),
                  "trades": len(d["trades"])}
        if record and not missing:
            d["marks"].append({"time": result["time"], "equity": result["equity"], "cash": result["cash"]})
            self.save()
        return result
