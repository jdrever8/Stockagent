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


def is_london(symbol: str) -> bool:
    return symbol.upper().endswith(".L")


def _normalise(price: float, currency: str) -> Tuple[float, str]:
    """Yahoo reports London pence as 'GBp' (sometimes 'GBX'); convert to pounds."""
    if currency in ("GBp", "GBX"):
        return price / 100.0, "GBP"
    return price, currency.upper()


def _yahoo_chart(symbol: str, rng: str, interval: str) -> dict:
    ysym = "GBPUSD=X" if symbol == "GBPUSD" else symbol.upper()
    resp = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}",
                        params={"range": rng, "interval": interval}, headers=UA, timeout=20)
    resp.raise_for_status()
    return resp.json()["chart"]["result"][0]


def _yahoo(symbol: str) -> Quote:
    r = _yahoo_chart(symbol, "1d", "1m")
    meta = r["meta"]
    price, ccy = _normalise(float(meta["regularMarketPrice"]), meta.get("currency") or "")
    ts = dt.datetime.fromtimestamp(meta["regularMarketTime"], dt.timezone.utc).isoformat()
    return Quote(symbol, price, "USD" if symbol == "GBPUSD" else ccy, ts, "yahoo")


def _stooq(symbols: List[str]) -> Dict[str, Quote]:
    """Fallback for US symbols and GBP/USD only: Stooq gives no currency, and London
    lines mix pence (shares) and pounds (many ETFs), so they are never guessed."""
    codes = {("GBPUSD" if s == "GBPUSD" else s.upper() + ".US"): s for s in symbols if not is_london(s)}
    if not codes:
        return {}
    url = "https://stooq.com/q/l/?s=" + "+".join(c.lower() for c in codes) + "&f=sd2t2ohlcv&h&e=csv"
    resp = requests.get(url, headers=UA, timeout=20)
    resp.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(resp.text)):
        sym = codes.get((row.get("Symbol") or "").upper())
        close = row.get("Close", "N/D")
        if sym and close not in ("N/D", "", None):
            out[sym] = Quote(sym, float(close), "USD", f"{row.get('Date')}T{row.get('Time')}", "stooq")
    return out


def fetch_quotes(symbols: List[str]) -> Dict[str, Quote]:
    """Live quotes in major units (pounds, not pence). Symbols with no reliable quote are omitted."""
    symbols = [s.upper() for s in symbols]
    out: Dict[str, Quote] = {}
    for s in symbols:
        try:
            q = _yahoo(s)
            if q.currency:
                out[s] = q
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
            pass
    missing = [s for s in symbols if s not in out]
    if missing:
        try:
            out.update(_stooq(missing))
        except requests.RequestException:
            pass
    return out


def fetch_history(symbol: str, rng: str = "2y") -> Tuple[List[Tuple[str, float]], str]:
    """Daily closes (date, close) oldest first, in major units, plus the currency."""
    r = _yahoo_chart(symbol, rng, "1d")
    closes = r["indicators"]["quote"][0]["close"]
    ccy = r["meta"].get("currency") or ""
    out = []
    for ts, c in zip(r["timestamp"], closes):
        if c is not None:
            price, norm = _normalise(float(c), ccy)
            if out and (price / out[-1][1] > 30 or price / out[-1][1] < 1 / 30):
                # London feeds occasionally print a bar in pence instead of pounds (or vice versa)
                price = price / 100 if price > out[-1][1] else price * 100
            out.append((dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d"), price))
    return out, _normalise(1.0, ccy)[1]


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
# ISA instruments. Derivatives, crypto, margin and short positions can't be held in an ISA;
# US-domiciled ETFs have no UK KID so UK platforms won't sell them (use the UCITS .L line).
INSTRUMENTS = ("share", "etf", "etp", "investment_trust", "aim", "gilt", "foreign_share", "crypto_etn")
STAMP_DUTY = 0.005          # SDRT on purchases of UK-incorporated shares and investment trusts
FX_FEE = 0.0015             # typical low-cost ISA platform FX charge on non-GBP trades


def costs(side: str, symbol: str, instrument: str, value_base: float, currency: str, base: str,
          dealing_fee: float) -> Dict[str, float]:
    stamp = STAMP_DUTY * value_base if (side == "buy" and is_london(symbol)
                                        and instrument in ("share", "investment_trust")) else 0.0
    fx = FX_FEE * value_base if currency != base else 0.0
    return {"dealing": dealing_fee, "stamp_duty": round(stamp, 2), "fx": round(fx, 2)}


class Book:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        if not os.path.exists(path):
            raise FileNotFoundError(f"no paper book at {path}")
        with open(path, encoding="utf-8") as fh:
            self.data = json.load(fh)

    @staticmethod
    def init(path: str, capital: float, base: str = "GBP", fee: float = 0.0, owner: str = "agent",
             force: bool = False, isa: bool = True, strategy: str = "") -> "Book":
        if os.path.exists(path) and not force:
            raise FileExistsError(f"{path} exists; pass --force to start over")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        data = {"owner": owner, "strategy": strategy, "base": base, "start_capital": capital, "created": now(),
                "cash": capital, "fee_per_trade": fee, "isa": isa, "eliminated": None,
                "positions": {}, "trades": [], "marks": [],
                "rules": ("ISA: ISA-eligible instruments only, no margin, no shorting, no loans; "
                          "zero funds = eliminated; everything else is the agent's choice")
                if isa else "none: any symbol, any size, shorting allowed; buys limited to cash"}
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
              source: Optional[str] = None, fx: Optional[float] = None, note: str = "",
              instrument: str = "share", currency: Optional[str] = None) -> dict:
        d = self.data
        side, symbol = side.lower(), symbol.upper()
        if d.get("eliminated"):
            raise PermissionError(f"{d['owner']} was eliminated on {d['eliminated']}")
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if qty <= 0:
            raise ValueError("quantity must be positive")
        if instrument not in INSTRUMENTS:
            raise ValueError(f"instrument must be one of {INSTRUMENTS}")
        if d.get("isa") and instrument == "crypto_etn":
            raise ValueError("crypto ETNs left the stocks & shares ISA on 6 April 2026 (IFISA only)")
        if d.get("isa") and instrument == "etf" and not is_london(symbol):
            raise ValueError("US-domiciled ETFs can't be bought in a UK ISA; use the UCITS London listing")
        held = d["positions"].get(symbol, {}).get("qty", 0.0)
        if d.get("isa") and side == "sell" and qty > held + 1e-9:
            raise ValueError(f"no shorting in an ISA: you hold {held} {symbol}")
        if price is None:
            quote = fetch_quotes([symbol]).get(symbol)
            if not quote:
                raise RuntimeError(f"no live quote for {symbol}; pass --price, --currency and --source")
            price, ccy, src, qtime = quote.price, quote.currency, quote.source, quote.time
        else:
            if not source:
                raise ValueError("a manual --price needs --source saying where the price came from")
            ccy = (currency or ("GBP" if is_london(symbol) else "USD")).upper()
            src, qtime = f"manual: {source}", now()
        pos = d["positions"].get(symbol)
        if pos and pos["currency"] != ccy:
            raise ValueError(f"{symbol} is held in {pos['currency']} but quoted in {ccy}")
        rate, fx_src = self.fx_to_base(ccy, manual_fx=fx)
        value = qty * price * rate
        fee = costs(side, symbol, instrument, value, ccy, d["base"], float(d.get("fee_per_trade", 0.0)))
        total_fee = round(sum(fee.values()), 2)
        if side == "buy" and value + total_fee > d["cash"] + 1e-9:
            max_qty = max(0.0, (d["cash"] - total_fee) / (price * rate))
            raise ValueError(f"buy needs {value + total_fee:,.2f} {d['base']} but cash is {d['cash']:,.2f} "
                             f"(no margin); max quantity about {max_qty:.4f}")
        pos = d["positions"].setdefault(symbol, {"qty": 0.0, "cost_base": 0.0, "currency": ccy,
                                                  "instrument": instrument})
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
            if opening > 0:   # flipped through zero (only possible outside an ISA)
                pos["cost_base"] = opening * price * rate
        pos["qty"] = round(pos["qty"] + signed, 8)
        d["cash"] += (-value if side == "buy" else value) - total_fee
        entry = {"time": now(), "side": side, "symbol": symbol, "instrument": instrument, "qty": qty,
                 "price": price, "currency": ccy, "fx_to_base": round(rate, 6), "value_base": round(value, 2),
                 "costs": fee, "realised_pnl": round(realised - (total_fee if side == "sell" else 0), 2),
                 "price_source": src, "quote_time": qtime, "fx_source": fx_src, "note": note}
        d["trades"].append(entry)
        if abs(pos["qty"]) < 1e-9:
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
        result = {"owner": d.get("owner"), "time": now(), "base": d["base"], "cash": round(d["cash"], 2),
                  "equity": round(total, 2), "return": total / d["start_capital"] - 1, "positions": rows,
                  "unpriced": missing, "trades": len(d["trades"]), "eliminated": d.get("eliminated"),
                  "realised_pnl": round(sum(t.get("realised_pnl", 0) for t in d["trades"]), 2)}
        if not missing and total <= 0.005 and not d.get("eliminated"):
            d["eliminated"] = result["time"]          # zero funds: the agent loses
            result["eliminated"] = d["eliminated"]
            self.save()
        if record and not missing:
            d["marks"].append({"time": result["time"], "equity": result["equity"], "cash": result["cash"]})
            self.save()
        return result
