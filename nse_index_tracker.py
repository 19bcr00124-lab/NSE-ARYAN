#!/usr/bin/env python3
"""
NSE Index Tracker
==================

Tracks live values of NSE (National Stock Exchange of India) indices such as
NIFTY 50, NIFTY BANK, NIFTY IT, etc. using NSE India's public JSON API.

Usage examples
--------------
List all indices once:
    python nse_index_tracker.py

Track specific indices, refreshing every 15 seconds, logging to CSV:
    python nse_index_tracker.py --indices "NIFTY 50" "NIFTY BANK" --interval 15 --log nifty_log.csv

Track continuously until Ctrl+C:
    python nse_index_tracker.py --indices "NIFTY 50" --watch

Track only sectoral indices, ranked by today's volatility:
    python nse_index_tracker.py --sectoral

Same, but watch it live every 30s and log to CSV:
    python nse_index_tracker.py --sectoral --watch --interval 30 --log sectoral_log.csv

Notes
-----
- NSE's website actively blocks non-browser-like requests. This script warms
  up a session by first visiting the homepage (to collect cookies) before
  calling the API, and sends browser-like headers. If NSE changes its
  anti-bot measures, you may need to update headers/cookies logic below.
- No third-party packages are required beyond `requests`.
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime

import requests

NSE_HOME_URL = "https://www.nseindia.com"
NSE_INDICES_URL = "https://www.nseindia.com/api/allIndices"

# NSE sectoral indices (as listed under NSE's "Sectoral Indices" category).
SECTORAL_INDICES = {
    "NIFTY AUTO",
    "NIFTY BANK",
    "NIFTY FIN SERVICE",
    "NIFTY FMCG",
    "NIFTY IT",
    "NIFTY MEDIA",
    "NIFTY METAL",
    "NIFTY PHARMA",
    "NIFTY PSU BANK",
    "NIFTY PVT BANK",
    "NIFTY REALTY",
    "NIFTY HEALTHCARE INDEX",
    "NIFTY CONSUMER DURABLES",
    "NIFTY OIL & GAS",
    "NIFTY ENERGY",
    "NIFTY FINANCIAL SERVICES 25/50",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-market-indices",
    "Connection": "keep-alive",
}


class NSEIndexTracker:
    """Fetches and tracks live NSE index data."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._warm_up()

    def _warm_up(self):
        """Visit the NSE homepage first to obtain cookies required by the API."""
        try:
            self.session.get(NSE_HOME_URL, timeout=10)
        except requests.RequestException as exc:
            print(f"Warning: could not warm up session ({exc}). Continuing anyway...")

    def fetch_all_indices(self, retries: int = 3):
        """Fetch data for all NSE indices. Returns a list of dicts."""
        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                resp = self.session.get(NSE_INDICES_URL, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", [])
                elif resp.status_code == 401 or resp.status_code == 403:
                    # Session likely stale; re-warm and retry
                    self._warm_up()
                else:
                    print(f"Attempt {attempt}: HTTP {resp.status_code}, retrying...")
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                print(f"Attempt {attempt}: error fetching data ({exc}), retrying...")
            time.sleep(1.5)
        raise RuntimeError(f"Failed to fetch NSE index data after {retries} attempts. Last error: {last_exc}")

    def get_indices(self, names=None):
        """Fetch and optionally filter indices by name (case-insensitive)."""
        all_data = self.fetch_all_indices()
        if not names:
            return all_data
        wanted = {n.strip().upper() for n in names}
        return [row for row in all_data if row.get("index", "").upper() in wanted]

    def get_sectoral_indices(self):
        """Fetch and filter to only NSE sectoral indices."""
        all_data = self.fetch_all_indices()
        return [row for row in all_data if row.get("index", "").upper() in SECTORAL_INDICES]


def format_row(row: dict) -> str:
    name = row.get("index", "N/A")
    last = row.get("last", "N/A")
    change = row.get("variation", 0) or 0
    pct = row.get("percentChange", 0) or 0
    sign = "+" if change >= 0 else ""
    arrow = "^" if change >= 0 else "v"
    return f"{name:<20} {last:>12} {sign}{change:>10.2f} {sign}{pct:>8.2f}%  {arrow}"


def print_table(rows):
    if not rows:
        print("No index data available.")
        return
    header = f"{'Index':<20} {'Last':>12} {'Change':>11} {'% Change':>9}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(format_row(row))


def compute_volatility(row: dict) -> float:
    """
    Approximate today's volatility for an index using its intraday range:
        (day's high - day's low) / previous close * 100

    This is a standard, widely used proxy for intraday volatility when
    tick-by-tick data isn't available (similar in spirit to a single-period
    Parkinson range estimate).
    """
    try:
        high = float(row.get("high") or 0)
        low = float(row.get("low") or 0)
        prev_close = float(row.get("previousClose") or 0)
        if prev_close <= 0:
            return 0.0
        return (high - low) / prev_close * 100
    except (TypeError, ValueError):
        return 0.0


def rank_by_volatility(rows):
    """Return rows sorted descending by intraday volatility, with the metric attached."""
    enriched = []
    for row in rows:
        vol = compute_volatility(row)
        enriched.append({**row, "_volatility": vol})
    return sorted(enriched, key=lambda r: r["_volatility"], reverse=True)


def print_volatility_table(rows):
    if not rows:
        print("No index data available.")
        return
    ranked = rank_by_volatility(rows)
    header = f"{'Rank':<5}{'Index':<26}{'Last':>12}{'% Change':>10}{'Day Range %':>13}"
    print(header)
    print("-" * len(header))
    for i, row in enumerate(ranked, start=1):
        name = row.get("index", "N/A")
        last = row.get("last", "N/A")
        pct = row.get("percentChange", 0) or 0
        vol = row["_volatility"]
        marker = "  <-- most volatile" if i == 1 else ""
        print(f"{i:<5}{name:<26}{last:>12}{pct:>9.2f}%{vol:>12.2f}%{marker}")
    print()
    top = ranked[0]
    print(f"Most volatile sectoral index today: {top.get('index')} "
          f"(day range {top['_volatility']:.2f}% of previous close, "
          f"currently {top.get('percentChange', 0):+.2f}%)")


def log_to_csv(rows, path):
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp", "index", "last", "change", "percent_change",
                "open", "high", "low", "previous_close", "day_range_volatility_pct"
            ])
        ts = datetime.now().isoformat(timespec="seconds")
        for row in rows:
            writer.writerow([
                ts,
                row.get("index"),
                row.get("last"),
                row.get("variation"),
                row.get("percentChange"),
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("previousClose"),
                round(compute_volatility(row), 4),
            ])


def main():
    parser = argparse.ArgumentParser(description="Track NSE (India) index values.")
    parser.add_argument(
        "--indices", nargs="*", default=None,
        help='Index names to track, e.g. "NIFTY 50" "NIFTY BANK". Omit to show all indices.'
    )
    parser.add_argument(
        "--sectoral", action="store_true",
        help="Track only NSE sectoral indices (Auto, Bank, IT, Pharma, FMCG, Metal, etc.), "
             "ranked by today's intraday volatility."
    )
    parser.add_argument("--watch", action="store_true", help="Keep polling continuously until Ctrl+C.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between refreshes when --watch is set (default: 30).")
    parser.add_argument("--log", type=str, default=None, help="Path to a CSV file to append each fetch's results to.")
    args = parser.parse_args()

    tracker = NSEIndexTracker()

    def run_once():
        try:
            if args.sectoral:
                rows = tracker.get_sectoral_indices()
            else:
                rows = tracker.get_indices(args.indices)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return
        print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        if args.sectoral:
            print_volatility_table(rows)
        else:
            print_table(rows)
        if args.log:
            log_to_csv(rows, args.log)

    run_once()

    if args.watch:
        try:
            while True:
                time.sleep(args.interval)
                run_once()
        except KeyboardInterrupt:
            print("\nStopped tracking.")


if __name__ == "__main__":
    main()
