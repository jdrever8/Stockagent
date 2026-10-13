"""Simulation constants.

Everything that comes from the paper (arXiv:2407.18957v5) is marked with the
section/table it was taken from. Companies C and D are a repo extension; their
numbers are not in the paper.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a local, git-ignored .env file. Real env vars win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, "").strip()
    return value or default


# ---------------------------------------------------------------------------
# Market calendar (paper 4.1.3 / 4.2.1)
# ---------------------------------------------------------------------------
TRADING_DAYS_PER_YEAR = 264
DAYS_PER_MONTH = 22
REPAYMENT_DAYS = [DAYS_PER_MONTH * m for m in range(1, 13)]  # interest paid on the last day of each month
SEASON_REPORT_DAYS = [12, 78, 144, 210]  # paper 4.2.1

# Loans (paper 4.1.3): annualised rates for 1/2/3 month loans; simple interest paid monthly.
LOAN_TERMS_DAYS = [22, 44, 66]
LOAN_RATES = [0.027, 0.030, 0.033]

# Transaction cost (paper 4.1.2): 0.005 per share, min 1, max 5.95 per transaction.
FEE_PER_SHARE = 0.005
FEE_MIN = 1.0
FEE_MAX = 5.95

# Initial endowment (paper 4.1.2)
MIN_INITIAL_PROPERTY = 100_000.0
MAX_INITIAL_PROPERTY = 5_000_000.0

PERSONALITIES = ["Conservative", "Aggressive", "Balanced", "Growth-Oriented"]

# ---------------------------------------------------------------------------
# Ideal price bounds (paper Tables 6 and 7; C/D are repo extensions)
# ---------------------------------------------------------------------------
IDEAL_PRICE_DAYS = [1, 12, 78, 144, 210]
IDEAL_PRICE_UPPER: Dict[str, List[float]] = {
    "A": [27.33, 48.18, 34.38, 49.34, 47.48],
    "B": [44.16, 42.16, 42.24, 42.01, 42.97],
    "C": [249.78, 222.34, 369.86, 333.01, 307.97],
    "D": [7.74, 7.58, 12.07, 8.12, 11.37],
}
IDEAL_PRICE_LOWER: Dict[str, List[float]] = {
    "A": [26.24, 45.70, 32.85, 46.80, 45.00],
    "B": [40.29, 38.52, 38.49, 38.28, 39.05],
    "C": [234.86, 211.90, 350.05, 314.41, 291.85],
    "D": [7.53, 7.38, 11.73, 7.87, 11.95],
}


def ideal_price_midpoint(stock: str, day: int) -> float:
    """Linear interpolation of the midpoint of the ideal price band."""
    mids = [(u + l) / 2 for u, l in zip(IDEAL_PRICE_UPPER[stock], IDEAL_PRICE_LOWER[stock])]
    days = IDEAL_PRICE_DAYS
    if day <= days[0]:
        return mids[0]
    for i in range(1, len(days)):
        if day <= days[i]:
            ratio = (day - days[i - 1]) / (days[i] - days[i - 1])
            return mids[i - 1] + ratio * (mids[i] - mids[i - 1])
    return mids[-1]


COMPANY_PROFILES: Dict[str, str] = {
    "A": "Listed for 10 years, chemical industry. Operations have hit bottlenecks and revenue declined "
         "over the past three years, but the overall trend is stable.",
    "B": "Technology company listed three years ago, in a growth phase. Revenue dipped last year with the "
         "tech sector but operations remain robust.",
    "C": "Mature large-cap with a 42% debt cost ratio and steady growth.",
    "D": "Mature, highly leveraged (56% debt cost ratio) low-growth company.",
}

# Twelve quarters of history shown to agents on day 1 (millions).
HISTORY: Dict[str, Dict[str, List[float]]] = {
    "A": {
        "revenue": [3696.19, 3578.00, 3595.49, 3215.64, 3973.40, 3810.57, 3840.70, 3433.02, 4344.52, 4095.22, 4114.16, 3717.96],
        "net_profit": [127.71, 217.96, 360.76, 358.08, 650.89, 693.30, 433.23, 517.06, 712.74, 628.31, 250.50, 325.51],
        "cash_flow": [30.10, 135.41, 344.32, 279.56, 564.62, 642.81, 350.39, 493.41, 650.65, 579.00, 185.71, 273.13],
    },
    "B": {
        "revenue": [570.00, 774.00, 643.00, 995.00, 684.46, 934.37, 782.08, 1204.05, 788.29, 1100.32, 914.96, 1418.37],
        "net_profit": [85.97, 142.09, 87.54, 135.76, 132.80, 169.65, 194.94, 272.11, 225.17, 356.72],
        "cash_flow": [68.97, 90.17, 82.18, 124.77, 75.50, 123.52, 132.72, 153.76, 194.94, 261.11, 216.39, 345.66],
    },
    "C": {
        "revenue": [35015.73, 36595.79, 35484.08, 36814.05, 37134.12, 38902.45, 37654.89, 39201.33, 40123.56, 41867.90, 40567.12, 42234.78],
        "net_profit": [8939.52, 9485.99, 8554.15, 8977.47, 9876.32, 10423.57, 9123.46, 9589.01, 10789.65, 11234.57, 9876.54, 10345.68],
        "cash_flow": [8456.30, 8915.10, 8380.28, 9106.32, 9321.46, 9876.54, 8567.89, 9012.35, 10123.46, 10678.90, 9324.57, 9789.01],
    },
    "D": {
        "revenue": [17925.22, 17725.45, 17714.92, 17732.90, 17845.67, 17689.34, 17654.88, 17701.23, 17989.45, 17834.56, 17789.01, 17845.67],
        "net_profit": [1444.77, 1745.96, 1679.37, 1478.92, 1589.46, 1890.12, 1812.35, 1601.23, 1723.46, 2015.68, 1934.57, 1712.35],
        "cash_flow": [1193.82, 1830.15, 1709.49, 1382.28, 1487.65, 1789.01, 1712.35, 1490.12, 1612.35, 1901.23, 1823.46, 1601.23],
    },
}

QUARTERLY_REPORTS: Dict[str, List[str]] = {
    "A": [
        "Revenue growth (YoY) 9.49%, revenue 4483.99M, gross margin 41.05%, net profit 856.67M, cash flow 756.75M.",
        "Revenue growth (YoY) 7.38%, revenue 4417.79M, gross margin 35.68%, net profit 493.95M, cash flow 396.53M.",
        "Revenue growth (YoY) 8.70%, revenue 4041.30M, gross margin 37.45%, net profit 724.36M, cash flow 639.53M.",
        "Revenue growth (YoY) 7.75%, revenue 5024.04M, gross margin 42.47%, net profit 1031.21M, cash flow 945.50M.",
    ],
    "B": [
        "Revenue growth (YoY) 19.96%, revenue 1319.94M, gross margin 31.21%, net profit 224.92M, cash flow 208.73M.",
        "Revenue growth (YoY) 19.86%, revenue 1096.70M, gross margin 31.26%, net profit 186.77M, cash flow 181.69M.",
        "Revenue growth (YoY) 18.21%, revenue 1676.70M, gross margin 31.58%, net profit 278.33M, cash flow 266.15M.",
        "Revenue growth (YoY) 15.98%, revenue 1075.13M, gross margin 32.41%, net profit 181.16M, cash flow 161.20M.",
    ],
    "C": [
        "Revenue 40123.56M, net profit 10789.65M, cash flow 10123.46M, WACC 7.74%.",
        "Revenue 41867.90M, net profit 11234.57M, cash flow 10678.90M, WACC 7.11%.",
        "Revenue 40567.12M, net profit 9876.54M, cash flow 9324.57M, WACC 7.32%.",
        "Revenue 42234.78M, net profit 10345.68M, cash flow 9789.01M, WACC 7.32%.",
    ],
    "D": [
        "Revenue 17989.45M, net profit 1723.46M, cash flow 1612.35M, WACC 7.32%.",
        "Revenue 17834.56M, net profit 2015.68M, cash flow 1901.23M, WACC 6.48%.",
        "Revenue 17789.01M, net profit 1934.57M, cash flow 1823.46M, WACC 6.76%.",
        "Revenue 17845.67M, net profit 1712.35M, cash flow 1601.23M, WACC 6.76%.",
    ],
}


@dataclass
class MarketEvent:
    day: int
    message: str
    loan_rates: Optional[List[float]] = None


# Paper 4.3 / Table 1: monetary easing at D78, rate hike at D144.
DEFAULT_EVENTS = [
    MarketEvent(78, "The government has announced a reduction in the reserve requirement ratio. "
                    "Lending interest rates have been lowered.", [0.024, 0.027, 0.030]),
    MarketEvent(144, "The economy is overheating; the government has announced an interest rate hike and "
                     "balance sheet contraction. Company A expects last quarter's revenue to be 3% below "
                     "expectations; Company B expects it to be 2% above.", [0.0255, 0.0285, 0.0315]),
]


@dataclass
class SimConfig:
    stocks: List[str] = field(default_factory=lambda: ["A", "B"])
    days: int = 10
    sessions: int = 3
    seed: int = 42
    # Agents decide in a random order each session (paper 3.2 "random clock").
    forum_sample: int = 20          # max BBS posts shown to each agent (context budget)
    forum_chars: int = 300          # truncate each post
    enable_bbs: bool = True         # paper 5.5 ablations
    enable_reports: bool = True
    enable_loans: bool = True
    enable_events: bool = True
    events: List[MarketEvent] = field(default_factory=lambda: list(DEFAULT_EVENTS))
    out_dir: str = "res"
    competitive: bool = True        # tell agents they are ranked against each other

    def initial_price(self, stock: str) -> float:
        return round(ideal_price_midpoint(stock, 1), 2)
