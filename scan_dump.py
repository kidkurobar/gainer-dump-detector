"""
╔══════════════════════════════════════════════════════════════════════════════╗
║        GAINER DUMP DETECTOR — THE PRECISION HUNTER                         ║
║        Detect dump signals on crypto futures top gainers                    ║
║        TF: 30m / 1H  |  Binance Futures USDT Perpetual                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  STEP 1: Filter top gainers by 24H % change from Binance Futures           ║
║  STEP 2: Analyze dump signals:                                             ║
║    A) Bearish Divergence  — Price HH but RSI/MACD LH                      ║
║    B) Multi-Indicator     — MA cross down + DIF/DEA cross down             ║
║    C) Volume Divergence   — Price up but volume declining                  ║
║  STEP 3: Score & Confidence → HIGH / MEDIUM / LOW                          ║
║  STEP 4: Output CLI + HTML Dashboard + Telegram Alert (HIGH only)          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import requests
import pandas as pd
import numpy as np
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TOP_GAINERS     = 30        # top N gainers to scan
TIMEFRAMES      = ["30m", "1h"]
LIMIT           = 120       # candles per request
# Binance Futures endpoints (fallback chain)
FUTURES_ENDPOINTS = [
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
    "https://fapi3.binance.com",
    "https://fapi4.binance.com",
]
BASE_URL        = "https://fapi.binance.com"

# HTTP session with headers
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
})

# Indicator params
MA_FAST         = 5
MA_SLOW         = 10
EMA_FAST        = 12
EMA_SLOW        = 26
MACD_SIGNAL     = 9
RSI_PERIOD      = 14
RSI_OB          = 70
RSI_OS          = 30
CROSS_WINDOW    = 3
DIV_LOOKBACK    = 20        # bars to check for divergence
VOL_LOOKBACK    = 10        # bars to check volume trend

# Telegram
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")

# Output
HTML_OUTPUT     = os.getenv("HTML_OUTPUT", "index.html")

# ── Exclude non-crypto ─────────────────────────────────────────────────────────
STOCK_TOKENS = {
    'AAPL','AMZN','TSLA','NVDA','MSFT','GOOG','GOOGL','META','NFLX',
    'BABA','JD','BIDU','PDD','NIO','XPEV','LI','PLTR','HOOD','RIVN',
    'COIN','MSTR','MARA','RIOT','HUT','CLSK','BTBT',
    'ARM','AMD','INTC','QCOM','MU','ASML',
    'SOXL','SOXS','TQQQ','SQQQ','SPXL','SPXS',
    'SPY','QQQ','ARKK','GLD','SLV',
    'JPM','BAC','GS','MS','WFC','XOM','CVX','OXY',
    'DIS','NFLX','PARA',
}
LVRG_PATTERNS = ('UP', 'DOWN', 'BULL', 'BEAR', '3L', '3S', '2L', '2S')


def is_pure_crypto(symbol: str) -> bool:
    if not symbol.endswith('USDT'):
        return False
    base = symbol[:-4]
    if base in STOCK_TOKENS:
        return False
    for pat in LVRG_PATTERNS:
        if base.endswith(pat):
            return False
    return True


# ─── DATA ──────────────────────────────────────────────────────────────────────

def api_get(path, timeout=15):
    """Try multiple Binance endpoints with fallback."""
    last_err = None
    for base in FUTURES_ENDPOINTS:
        try:
            url = f"{base}{path}"
            resp = SESSION.get(url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            return data
        except Exception as e:
            last_err = e
            print(f"  ⚠️ {base} failed: {e}")
            continue
    print(f"  ❌ All endpoints failed. Last error: {last_err}")
    return None


def get_top_gainers(n=TOP_GAINERS):
    """Get top N gainers from Binance Futures sorted by 24H % change."""
    data = api_get("/fapi/v1/ticker/24hr")

    if data is None:
        return []

    # API error check
    if isinstance(data, dict):
        print(f"  ⚠️ API error: {data}")
        return []
    if not isinstance(data, list) or len(data) == 0:
        print(f"  ⚠️ Unexpected API response type: {type(data)}")
        return []

    crypto = [d for d in data if isinstance(d, dict) and 'symbol' in d and is_pure_crypto(d['symbol'])]
    # Sort by price change percent descending (top gainers)
    crypto.sort(key=lambda x: float(x.get('priceChangePercent', 0)), reverse=True)
    results = []
    for d in crypto[:n]:
        results.append({
            'symbol': d['symbol'],
            'change_pct': float(d.get('priceChangePercent', 0)),
            'volume_usdt': float(d.get('quoteVolume', 0)),
            'last_price': float(d.get('lastPrice', 0)),
        })
    return results


def get_klines(symbol, interval, limit=LIMIT):
    data = api_get(f"/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}", timeout=10)
    if data is None or not isinstance(data, list):
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=[
        'ts','o','h','l','c','v','ct','qa','tr','tb','tq','ignore'])
    for col in ['o','h','l','c','v']:
        df[col] = df[col].astype(float)
    df['qa'] = df['qa'].astype(float)
    return df


# ─── INDICATORS ────────────────────────────────────────────────────────────────

def calc_sma(s, period):
    return s.rolling(period).mean()

def calc_ema(s, span):
    return s.ewm(span=span, adjust=False).mean()

def calc_macd(s):
    ema_f = calc_ema(s, EMA_FAST)
    ema_s = calc_ema(s, EMA_SLOW)
    dif = ema_f - ema_s
    dea = calc_ema(dif, MACD_SIGNAL)
    hist = (dif - dea) * 2
    return dif, dea, hist

def calc_rsi(s, period=RSI_PERIOD):
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))


# ─── SIGNAL DETECTION ─────────────────────────────────────────────────────────

def find_cross_down(fast, slow, window=CROSS_WINDOW):
    """Check if fast crossed BELOW slow within last `window` bars (bearish)."""
    for i in range(-window - 1, -1):
        pf, ps = fast.iloc[i - 1], slow.iloc[i - 1]
        cf, cs = fast.iloc[i], slow.iloc[i]
        if pf > ps and cf < cs:
            return True, abs(i) - 1
    return False, None


def detect_bearish_divergence(df, rsi, dif, lookback=DIV_LOOKBACK):
    """
    Bearish Divergence: Price makes Higher High but RSI or MACD DIF makes Lower High.
    Returns (rsi_div, macd_div)
    """
    if len(df) < lookback + 5:
        return False, False

    # Compare last closed candle vs peak in lookback window before it
    price_now = df['h'].iloc[-2]
    price_prev = df['h'].iloc[-lookback-2:-2].max()

    rsi_now = rsi.iloc[-2]
    rsi_prev = rsi.iloc[-lookback-2:-2].max()

    dif_now = dif.iloc[-2]
    dif_prev = dif.iloc[-lookback-2:-2].max()

    # Price HH but indicator LH
    rsi_div = (price_now >= price_prev * 0.998) and (rsi_now < rsi_prev - 2)
    macd_div = (price_now >= price_prev * 0.998) and (dif_now < dif_prev * 0.95)

    return rsi_div, macd_div


def detect_volume_divergence(df, lookback=VOL_LOOKBACK):
    """
    Volume Divergence: Price trending up but volume declining.
    Returns True if price up and volume down over lookback period.
    """
    if len(df) < lookback + 2:
        return False, 0.0

    prices = df['c'].iloc[-lookback-1:-1]
    volumes = df['v'].iloc[-lookback-1:-1]

    # Simple linear slope comparison
    x = np.arange(len(prices))
    price_slope = np.polyfit(x, prices.values, 1)[0]
    vol_slope = np.polyfit(x, volumes.values, 1)[0]

    # Price going up but volume going down
    vol_div = price_slope > 0 and vol_slope < 0

    # Volume decline ratio (how much volume dropped)
    vol_start = volumes.iloc[:3].mean()
    vol_end = volumes.iloc[-3:].mean()
    vol_change_pct = ((vol_end - vol_start) / (vol_start + 1e-10)) * 100

    return vol_div, vol_change_pct


# ─── MAIN ANALYZER ────────────────────────────────────────────────────────────

def analyze_dump_signal(symbol, interval, gainer_info):
    """Analyze a single symbol on a single timeframe for dump signals."""
    try:
        df = get_klines(symbol, interval)
        if len(df) < 50:
            return None

        # Calculate indicators
        ma5 = calc_sma(df['c'], MA_FAST)
        ma10 = calc_sma(df['c'], MA_SLOW)
        ema12 = calc_ema(df['c'], EMA_FAST)
        ema26 = calc_ema(df['c'], EMA_SLOW)
        dif, dea, macd_hist = calc_macd(df['c'])
        rsi = calc_rsi(df['c'])

        price = df['c'].iloc[-2]  # last closed candle

        # ── DUMP SIGNAL SCORING ──
        dump_score = 0
        signals = []

        # 1. Bearish RSI Divergence
        rsi_div, macd_div = detect_bearish_divergence(df, rsi, dif)
        if rsi_div:
            dump_score += 2  # strong signal
            signals.append("🔴 RSI Bearish Divergence (Price HH, RSI LH)")
        if macd_div:
            dump_score += 2
            signals.append("🔴 MACD Bearish Divergence (Price HH, DIF LH)")

        # 2. MA5 cross down MA10
        ma_cross, ma_bars = find_cross_down(ma5, ma10)
        if ma_cross:
            dump_score += 1
            signals.append(f"📉 MA5 cross below MA10 ({ma_bars} bar ago)")

        # 3. DIF cross down DEA
        dif_cross, dif_bars = find_cross_down(dif, dea)
        if dif_cross:
            dump_score += 1
            signals.append(f"📉 DIF cross below DEA ({dif_bars} bar ago)")

        # 4. MACD Histogram flip negative
        hist_now = macd_hist.iloc[-2]
        hist_prev = macd_hist.iloc[-3]
        if hist_now < 0 and hist_prev > 0:
            dump_score += 1
            signals.append(f"📉 MACD Hist flipped negative ({hist_now:.5g})")
        elif hist_now < 0:
            dump_score += 0.5
            signals.append(f"⚠️ MACD Hist negative ({hist_now:.5g})")

        # 5. RSI Overbought
        rsi_now = rsi.iloc[-2]
        if rsi_now > RSI_OB:
            dump_score += 1
            signals.append(f"🔴 RSI Overbought ({rsi_now:.1f})")
        elif rsi_now > 65:
            dump_score += 0.5
            signals.append(f"⚠️ RSI Elevated ({rsi_now:.1f})")

        # 6. Volume Divergence
        vol_div, vol_change = detect_volume_divergence(df)
        if vol_div:
            dump_score += 1.5
            signals.append(f"📉 Volume Divergence (vol {vol_change:.1f}%)")

        # 7. Price far above EMA26 (overextended)
        ema26_now = ema26.iloc[-2]
        distance_pct = ((price - ema26_now) / ema26_now) * 100
        if distance_pct > 5:
            dump_score += 1
            signals.append(f"⚠️ Overextended +{distance_pct:.1f}% above EMA26")
        elif distance_pct > 3:
            dump_score += 0.5
            signals.append(f"⚠️ Stretched +{distance_pct:.1f}% above EMA26")

        # ── No dump signal ──
        if dump_score < 2:
            return None

        # ── Confidence ──
        if dump_score >= 5:
            confidence = "HIGH"
        elif dump_score >= 3:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Only return HIGH and MEDIUM
        if confidence == "LOW":
            return None

        return {
            'symbol': symbol,
            'interval': interval,
            'price': price,
            'change_24h': gainer_info['change_pct'],
            'volume_usdt': gainer_info['volume_usdt'],
            'dump_score': dump_score,
            'confidence': confidence,
            'signals': signals,
            'rsi': rsi_now,
            'macd_hist': hist_now,
            'ma5': ma5.iloc[-2],
            'ma10': ma10.iloc[-2],
            'ema12': ema12.iloc[-2],
            'ema26': ema26_now,
            'dif': dif.iloc[-2],
            'dea': dea.iloc[-2],
            'distance_ema26': distance_pct,
            'vol_divergence': vol_div,
            'rsi_divergence': rsi_div,
            'macd_divergence': macd_div,
        }

    except Exception as e:
        return None


# ─── CLI OUTPUT ────────────────────────────────────────────────────────────────

CONF_ICON = {'HIGH': '⚡⚡HIGH', 'MEDIUM': '⚡MED'}

def print_cli(results, gainers):
    now = datetime.now(timezone(timedelta(hours=7))).strftime('%Y-%m-%d %H:%M:%S GMT+7')
    print("=" * 100)
    print(f"  🎯 GAINER DUMP DETECTOR — THE PRECISION HUNTER")
    print(f"  Scanning top {len(gainers)} gainers on [30m, 1H]")
    print(f"  {now}")
    print("=" * 100)

    if not results:
        print("\n  ✅ No dump signals detected among top gainers.")
        print("=" * 100)
        return

    # Sort by confidence then score
    conf_order = {'HIGH': 0, 'MEDIUM': 1}
    results.sort(key=lambda x: (conf_order[x['confidence']], -x['dump_score']))

    for conf in ['HIGH', 'MEDIUM']:
        items = [r for r in results if r['confidence'] == conf]
        if not items:
            continue
        print(f"\n  ── {CONF_ICON[conf]} ({len(items)} signals) {'─'*60}")
        for r in items:
            print(f"\n  {r['symbol']} [{r['interval']}]  |  24H: +{r['change_24h']:.1f}%  |  "
                  f"Dump Score: {r['dump_score']:.1f}  |  RSI: {r['rsi']:.1f}")
            for s in r['signals']:
                print(f"    {s}")

    # Hunter format for HIGH
    high = [r for r in results if r['confidence'] == 'HIGH']
    if high:
        print(f"\n{'═'*64}")
        print(f"  🎯 DUMP ALERT — HIGH CONFIDENCE")
        print(f"{'═'*64}")
        for r in high:
            sl_pct = 0.5
            tp_pct = 1.5
            sl = r['price'] * (1 + sl_pct / 100)
            tp = r['price'] * (1 - tp_pct / 100)
            print(f"\n  {'═'*60}")
            print(f"  {r['symbol']}  [{r['interval']}]  HIGH CONFIDENCE DUMP")
            print(f"  {'═'*60}")
            print(f"  Market Bias : Bearish (Gainer exhaustion)")
            print(f"  24H Change  : +{r['change_24h']:.1f}%")
            print(f"  Entry SHORT : {r['price']:.6g}")
            print(f"  Stop Loss   : {sl:.6g}  (+{sl_pct}%)")
            print(f"  Take Profit : {tp:.6g}  (-{tp_pct}%)")
            print(f"  Risk %      : {sl_pct}%")
            print(f"  Confidence  : HIGH  (Score: {r['dump_score']:.1f})")
            print(f"")
            print(f"  Reason:")
            for s in r['signals'][:5]:
                print(f"   • {s}")
            print(f"  {'─'*60}")

    h = len([r for r in results if r['confidence'] == 'HIGH'])
    m = len([r for r in results if r['confidence'] == 'MEDIUM'])
    print(f"\n  Total: {len(results)}  |  ⚡⚡HIGH: {h}  |  ⚡MED: {m}")
    print(f"  FACT: Signals based on divergence + crossover + volume analysis")
    print(f"  INFERENCE: Score reflects multi-indicator dump probability")
    print(f"  SPECULATION: SL/TP are estimates — verify with your own risk sizing")
    print("=" * 100)


# ─── HTML DASHBOARD ────────────────────────────────────────────────────────────

def generate_html(results, gainers):
    now = datetime.now(timezone(timedelta(hours=7))).strftime('%Y-%m-%d %H:%M:%S GMT+7')

    # Sort results
    conf_order = {'HIGH': 0, 'MEDIUM': 1}
    results.sort(key=lambda x: (conf_order[x['confidence']], -x['dump_score']))

    # Gainers table data
    gainers_json = json.dumps(gainers, default=str)
    results_json = json.dumps(results, default=str)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="300">
<title>Gainer Dump Detector — Precision Hunter</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #f5f6fa;
    color: #1a1a2e;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    padding: 20px;
    min-height: 100vh;
  }}
  .header {{
    text-align: center;
    padding: 24px;
    background: linear-gradient(135deg, #ffffff, #e8ecf4);
    border-radius: 12px;
    margin-bottom: 20px;
    border: 1px solid #d0d5e0;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  }}
  .header h1 {{
    font-size: 1.6em;
    background: linear-gradient(90deg, #d32f2f, #e65100);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }}
  .header .subtitle {{ color: #666; font-size: 0.9em; }}
  .header .updated {{ color: #999; font-size: 0.8em; margin-top: 6px; }}

  .stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }}
  .stat-card {{
    background: #ffffff;
    border: 1px solid #e0e3ea;
    border-radius: 10px;
    padding: 16px;
    text-align: center;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  }}
  .stat-card .value {{ font-size: 1.8em; font-weight: 700; }}
  .stat-card .label {{ font-size: 0.75em; color: #888; margin-top: 4px; }}
  .high {{ color: #d32f2f; }}
  .medium {{ color: #e65100; }}
  .green {{ color: #2e7d32; }}

  .section-title {{
    font-size: 1.1em;
    font-weight: 600;
    padding: 12px 0 8px;
    border-bottom: 1px solid #ddd;
    margin-bottom: 12px;
    color: #333;
  }}

  .alert-card {{
    background: #fff5f5;
    border: 1px solid #ffcdd2;
    border-left: 4px solid #d32f2f;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  }}
  .alert-card.medium {{
    background: #fff8e1;
    border-color: #ffe0b2;
    border-left-color: #e65100;
  }}
  .alert-card .top-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 10px;
  }}
  .alert-card .symbol {{
    font-size: 1.2em;
    font-weight: 700;
    color: #1a1a2e;
  }}
  .alert-card .badge {{
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.75em;
    font-weight: 600;
  }}
  .badge-high {{ background: #d32f2f; color: #fff; }}
  .badge-med {{ background: #e65100; color: #fff; }}
  .alert-card .meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    font-size: 0.85em;
    color: #555;
    margin-bottom: 10px;
  }}
  .alert-card .signals {{
    font-size: 0.82em;
    line-height: 1.6;
    color: #444;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85em;
    margin-top: 8px;
    background: #fff;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  }}
  th {{
    background: #f0f2f8;
    color: #555;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 0.75em;
    padding: 10px 12px;
    text-align: left;
    cursor: pointer;
    user-select: none;
    border-bottom: 2px solid #ddd;
  }}
  th:hover {{ color: #1a1a2e; background: #e4e8f0; }}
  td {{
    padding: 10px 12px;
    border-bottom: 1px solid #f0f0f5;
  }}
  tr:hover td {{ background: #f5f7fc; }}
  .pct-up {{ color: #2e7d32; font-weight: 600; }}
  .pct-down {{ color: #d32f2f; font-weight: 600; }}

  .footer {{
    text-align: center;
    padding: 20px;
    color: #aaa;
    font-size: 0.75em;
    margin-top: 30px;
  }}

  @media (max-width: 640px) {{
    body {{ padding: 10px; }}
    .header h1 {{ font-size: 1.2em; }}
    .stats {{ grid-template-columns: repeat(2, 1fr); }}
    .alert-card .meta {{ flex-direction: column; gap: 4px; }}
    table {{ font-size: 0.78em; }}
    td, th {{ padding: 6px 8px; }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>🎯 GAINER DUMP DETECTOR</h1>
  <div class="subtitle">Precision Hunter — Bearish Signal Scanner on Top Gainers</div>
  <div class="updated">Last scan: {now} · Auto-refresh every 5 min</div>
</div>

<div class="stats">
  <div class="stat-card">
    <div class="value green">{len(gainers)}</div>
    <div class="label">Gainers Scanned</div>
  </div>
  <div class="stat-card">
    <div class="value" style="color:#1a1a2e">{len(results)}</div>
    <div class="label">Dump Signals</div>
  </div>
  <div class="stat-card">
    <div class="value high">{len([r for r in results if r['confidence']=='HIGH'])}</div>
    <div class="label">HIGH Confidence</div>
  </div>
  <div class="stat-card">
    <div class="value medium">{len([r for r in results if r['confidence']=='MEDIUM'])}</div>
    <div class="label">MEDIUM Confidence</div>
  </div>
</div>

<!-- DUMP ALERTS -->
<div class="section-title">🔴 Dump Signals</div>
"""

    if not results:
        html += '<div style="text-align:center;padding:40px;color:#999;">✅ No dump signals — top gainers look clean</div>\n'
    else:
        for r in results:
            conf = r['confidence']
            badge_cls = 'badge-high' if conf == 'HIGH' else 'badge-med'
            card_cls = '' if conf == 'HIGH' else 'medium'
            signals_html = '<br>'.join(r['signals'])
            html += f"""<div class="alert-card {card_cls}">
  <div class="top-row">
    <span class="symbol">{r['symbol']} <span style="color:#666;font-size:0.7em">[{r['interval']}]</span></span>
    <span class="badge {badge_cls}">{conf} · Score {r['dump_score']:.1f}</span>
  </div>
  <div class="meta">
    <span>💰 Price: {r['price']:.6g}</span>
    <span>📈 24H: +{r['change_24h']:.1f}%</span>
    <span>📊 RSI: {r['rsi']:.1f}</span>
    <span>📉 MACD: {r['macd_hist']:.5g}</span>
    <span>↕️ EMA26 dist: +{r['distance_ema26']:.1f}%</span>
  </div>
  <div class="signals">{signals_html}</div>
</div>
"""

    # Gainers table
    html += """
<div class="section-title" style="margin-top:24px">📊 Top Gainers (24H)</div>
<table id="gainerTable">
<thead>
<tr>
  <th onclick="sortTable(0)">#</th>
  <th onclick="sortTable(1)">Symbol</th>
  <th onclick="sortTable(2)">Price</th>
  <th onclick="sortTable(3)">24H %</th>
  <th onclick="sortTable(4)">Volume (USDT)</th>
  <th>Status</th>
</tr>
</thead>
<tbody>
"""
    dump_symbols = {(r['symbol'], r['interval']): r for r in results}
    for i, g in enumerate(gainers):
        # Check if this gainer has any dump signal
        status = "✅ Clean"
        status_style = "color:#444"
        for tf in TIMEFRAMES:
            key = (g['symbol'], tf)
            if key in dump_symbols:
                r = dump_symbols[key]
                if r['confidence'] == 'HIGH':
                    status = f"🔴 HIGH [{tf}]"
                    status_style = "color:#ff4444;font-weight:700"
                elif r['confidence'] == 'MEDIUM':
                    status = f"🟠 MED [{tf}]"
                    status_style = "color:#ff8800"
                break

        vol_fmt = f"{g['volume_usdt']/1e6:.1f}M" if g['volume_usdt'] > 1e6 else f"{g['volume_usdt']/1e3:.0f}K"
        html += f"""<tr>
  <td>{i+1}</td>
  <td style="font-weight:600">{g['symbol']}</td>
  <td>{g['last_price']:.6g}</td>
  <td class="pct-up">+{g['change_pct']:.2f}%</td>
  <td>{vol_fmt}</td>
  <td style="{status_style}">{status}</td>
</tr>
"""

    html += f"""</tbody>
</table>

<div class="footer">
  FACT: Signals based on divergence + crossover + volume analysis<br>
  INFERENCE: Dump score reflects multi-indicator alignment<br>
  SPECULATION: Not financial advice — verify with your own analysis<br>
  <br>Precision Hunter · Gainer Dump Detector · Auto-scan via GitHub Actions
</div>

<script>
function sortTable(colIdx) {{
  const table = document.getElementById('gainerTable');
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const asc = table.dataset.sortCol == colIdx && table.dataset.sortDir !== 'asc';
  table.dataset.sortCol = colIdx;
  table.dataset.sortDir = asc ? 'asc' : 'desc';
  rows.sort((a, b) => {{
    let va = a.cells[colIdx].textContent.trim();
    let vb = b.cells[colIdx].textContent.trim();
    // Try numeric
    const na = parseFloat(va.replace(/[^\\d.\\-]/g, ''));
    const nb = parseFloat(vb.replace(/[^\\d.\\-]/g, ''));
    if (!isNaN(na) && !isNaN(nb)) {{
      return asc ? na - nb : nb - na;
    }}
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  }});
  rows.forEach(r => tbody.appendChild(r));
}}
</script>
</body>
</html>"""

    return html


# ─── TELEGRAM ALERT ────────────────────────────────────────────────────────────

def send_telegram(results):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return

    high = [r for r in results if r['confidence'] == 'HIGH']
    if not high:
        return

    lines = ["🎯 *GAINER DUMP DETECTOR — HIGH ALERT*\n"]
    for r in high:
        sl = r['price'] * 1.005
        tp = r['price'] * 0.985
        lines.append(f"🔴 *{r['symbol']}* `[{r['interval']}]`")
        lines.append(f"  24H: +{r['change_24h']:.1f}% | Score: {r['dump_score']:.1f}")
        lines.append(f"  Price: `{r['price']:.6g}`")
        lines.append(f"  SL: `{sl:.6g}` | TP: `{tp:.6g}`")
        lines.append(f"  RSI: {r['rsi']:.1f} | MACD: {r['macd_hist']:.5g}")
        for s in r['signals'][:4]:
            lines.append(f"  {s}")
        lines.append("")

    lines.append("_FACT: Multi-indicator dump signal_")
    lines.append("_SPECULATION: Verify with your own analysis_")

    text = "\n".join(lines)

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            'chat_id': TELEGRAM_CHAT,
            'text': text,
            'parse_mode': 'Markdown',
        }, timeout=10)
    except Exception as e:
        print(f"  ⚠️ Telegram send failed: {e}")


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    # 1. Get top gainers
    print("  Fetching top gainers from Binance Futures...")
    try:
        gainers = get_top_gainers()
    except Exception as e:
        print(f"  ⚠️ Failed to fetch gainers: {e}")
        gainers = []

    if not gainers:
        print("  ⚠️ No gainers found. Generating empty dashboard.")
        html = generate_html([], [])
        with open(HTML_OUTPUT, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"  📄 Empty dashboard saved: {HTML_OUTPUT}")
        return

    print(f"  Found {len(gainers)} top gainers (top: {gainers[0]['symbol']} +{gainers[0]['change_pct']:.1f}%)")

    # 2. Scan for dump signals
    print(f"  Scanning for dump signals on [{', '.join(TIMEFRAMES)}]...\n")
    all_results = []

    def scan_one(gainer):
        results = []
        for tf in TIMEFRAMES:
            r = analyze_dump_signal(gainer['symbol'], tf, gainer)
            if r:
                results.append(r)
        return results

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(scan_one, g): g for g in gainers}
        for f in as_completed(futures):
            res = f.result()
            if res:
                all_results.extend(res)

    # 3. CLI output
    print_cli(all_results, gainers)

    # 4. HTML Dashboard
    html = generate_html(all_results, gainers)
    output_path = HTML_OUTPUT
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n  📄 Dashboard saved: {output_path}")

    # 5. Telegram Alert
    send_telegram(all_results)
    high_count = len([r for r in all_results if r['confidence'] == 'HIGH'])
    if high_count:
        print(f"  📱 Telegram alert sent ({high_count} HIGH signals)")


if __name__ == "__main__":
    main()
