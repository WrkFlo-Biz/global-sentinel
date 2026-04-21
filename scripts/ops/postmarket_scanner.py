#!/usr/bin/env python3
"""
Global Sentinel — Post-Market Intelligence Scanner
Runs daily at 4:30 PM ET (after 4:00 close)

1. Scans today's biggest movers, highs/lows
2. Monitors after-hours news/headlines
3. Gets 52-week high/low and avg % move for top movers
4. Analyzes earnings reports and catalysts
5. Generates overnight/next-day trade suggestions
6. Sends report via Telegram
"""
import json, ssl, time, statistics
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

sys.path.insert(0, '/opt/global-sentinel') if '/opt/global-sentinel' not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

REPO_ROOT = Path('/opt/global-sentinel')
REPORT_DIR = REPO_ROOT / 'data' / 'market_intel'
REPORT_DIR.mkdir(parents=True, exist_ok=True)

env = {}
with open(REPO_ROOT / '.env') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        env[k.strip()] = v.strip().strip("'\"  ")

TG_TOKEN   = env.get('TELEGRAM_BOT_TOKEN', '')
TG_CHAT    = env.get('TELEGRAM_CHAT_ID', '')
ALP_KEY    = env.get('ALPACA_API_KEY_LIVE', '')
ALP_SECRET = env.get('ALPACA_SECRET_KEY_LIVE', '')
HEADERS    = {'APCA-API-KEY-ID': ALP_KEY, 'APCA-API-SECRET-KEY': ALP_SECRET}
DATA_BASE  = 'https://data.alpaca.markets'
ctx = ssl.create_default_context()

UNIVERSE = [
    'SPY','QQQ','IWM','DIA','TQQQ','SOXL','XLE','XLF','GDX','SLV','TLT','HYG','USO',
    'AAPL','MSFT','GOOGL','AMZN','META','NVDA','TSLA','JPM','V',
    'AMD','AVGO','MRVL','MU','SMCI','ARM','INTC',
    'NFLX','PLTR','COIN','HOOD','SHOP','SQ','UBER','ABNB','DKNG','RBLX',
    'CRWD','SNOW','NET','DDOG','MDB','ZS','PANW',
    'SOFI','MARA','RIOT','RIVN','NIO','LCID','PLUG','IONQ','RGTI','QBTS',
    'HIMS','RDDT','CVNA','CAVA','RKLB','LUNR','AFRM','UPST','OPEN',
    'XOM','CVX','FSLR','ENPH','RUN',
    'DAL','UAL','AAL','LUV',
    'BAC','GS','MS','C',
    'BABA','JD','PDD','KWEB',
    'IBIT','BITO','GBTC','MSTR',
]

def api_get(base, path):
    req = urllib.request.Request(base + path, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  [API ERR] {path[:60]}: {e}')
        return {}

def send_telegram(msg):
    if _send_topic:
        try:
            _send_topic(msg[:4000] if isinstance(msg, str) else str(msg)[:4000], topic='trading')
            return
        except Exception:
            pass
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        pd = {'chat_id': TG_CHAT, 'text': msg, 'parse_mode': 'HTML'}
        if str(TG_CHAT).startswith('-100'):
            dt = env.get('TELEGRAM_DEFAULT_THREAD_ID')
            if dt:
                pd['message_thread_id'] = int(dt)
        payload = json.dumps(pd).encode()
        req = urllib.request.Request(
            'https://api.telegram.org/bot' + TG_TOKEN + '/sendMessage',
            data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        print(f'  [TG] {e}')

def get_snapshots(symbols):
    all_snaps = {}
    for i in range(0, len(symbols), 25):
        chunk = symbols[i:i+25]
        syms = ','.join(chunk)
        data = api_get(DATA_BASE, '/v2/stocks/snapshots?symbols=' + syms + '&feed=iex')
        if data:
            all_snaps.update(data)
        time.sleep(0.3)
    return all_snaps

def get_historical_bars(symbol, days=252):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days + 30)
    path = '/v2/stocks/' + symbol + '/bars?timeframe=1Day&start=' + start.strftime('%Y-%m-%d') + '&end=' + end.strftime('%Y-%m-%d') + '&limit=300&feed=iex'
    data = api_get(DATA_BASE, path)
    return data.get('bars', [])

def get_news(limit=50):
    data = api_get(DATA_BASE, '/v1beta1/news?limit=' + str(limit))
    return data.get('news', data) if isinstance(data, dict) else data

def analyze_postmarket():
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    report_date = now_et.strftime('%Y-%m-%d')
    print('\n' + '='*60)
    print('  POST-MARKET INTELLIGENCE SCAN -- ' + report_date + ' ' + now_et.strftime('%H:%M') + ' ET')
    print('='*60 + '\n')

    # 1. Today's close data
    print('[1/5] Scanning end-of-day snapshots...')
    snaps = get_snapshots(UNIVERSE)

    movers = []
    for sym, data in snaps.items():
        try:
            trade = data.get('latestTrade', {})
            prev = data.get('prevDailyBar', {})
            daily = data.get('dailyBar', {})
            price = trade.get('p', 0)
            prev_close = prev.get('c', 0)
            day_high = daily.get('h', 0)
            day_low = daily.get('l', 0)
            day_open = daily.get('o', 0)
            day_vol = daily.get('v', 0)
            if not prev_close or not price:
                continue
            day_chg = (price - prev_close) / prev_close * 100
            day_range = (day_high - day_low) / prev_close * 100 if prev_close else 0
            close_vs_high = (price - day_high) / day_high * 100 if day_high else 0
            movers.append({
                'sym': sym, 'price': price, 'prev_close': prev_close,
                'day_chg': day_chg, 'day_high': day_high, 'day_low': day_low,
                'day_range': day_range, 'day_vol': day_vol,
                'close_vs_high': close_vs_high,
            })
        except Exception:
            continue

    movers.sort(key=lambda x: abs(x['day_chg']), reverse=True)

    # 2. 52-week data for top 20
    print('[2/5] Pulling 52-week data...')
    top20 = movers[:20]
    for m in top20:
        bars = get_historical_bars(m['sym'], days=252)
        if bars:
            highs = [b['h'] for b in bars]
            lows = [b['l'] for b in bars]
            closes = [b['c'] for b in bars]
            daily_moves = []
            for i in range(1, len(closes)):
                daily_moves.append(abs((closes[i] - closes[i-1]) / closes[i-1] * 100))
            m['wk52_high'] = max(highs) if highs else 0
            m['wk52_low'] = min(lows) if lows else 0
            m['avg_price'] = statistics.mean(closes) if closes else 0
            m['avg_daily_move'] = statistics.mean(daily_moves) if daily_moves else 0
            m['from_52h'] = (m['price'] - m['wk52_high']) / m['wk52_high'] * 100 if m['wk52_high'] else 0
            m['from_52l'] = (m['price'] - m['wk52_low']) / m['wk52_low'] * 100 if m['wk52_low'] else 0
        time.sleep(0.2)

    # 3. News — focus on after-hours, earnings
    print('[3/5] Scanning after-hours news...')
    news = get_news(50)
    earnings_news = []
    general_news = []
    for n in news:
        h = n.get('headline', '')
        syms = ','.join(n.get('symbols', []))
        entry = syms + ': ' + h
        if any(w in h.lower() for w in ['earn', 'q1', 'q2', 'q3', 'q4', 'quarter', 'beat', 'miss', 'revenue', 'guidance', 'eps', 'report']):
            earnings_news.append(entry)
        else:
            general_news.append(entry)

    # 4. Identify momentum continuation vs. reversal candidates
    print('[4/5] Scoring candidates...')
    idx_data = {}
    for m in movers:
        if m['sym'] in ['SPY', 'QQQ', 'IWM', 'DIA']:
            idx_data[m['sym']] = m['day_chg']

    spy_chg = idx_data.get('SPY', 0)
    market_mood = 'BULLISH' if spy_chg > 0.5 else ('BEARISH' if spy_chg < -0.5 else 'FLAT')

    candidates = []
    for m in top20:
        if not m.get('avg_daily_move'):
            continue
        score = 0
        # Strong close near high of day = momentum continuation
        if m['close_vs_high'] > -1.0 and m['day_chg'] > 0:
            score += 15
        # Big move today + above average = likely continuation
        if abs(m['day_chg']) > m.get('avg_daily_move', 5) * 1.5:
            score += 10
        # Earnings catalyst tonight
        for en in earnings_news:
            if m['sym'] in en:
                score += 20
                break
        # Volume confirmation
        score += m['day_chg'] * 2
        # Room to run (not at 52-week high)
        if m.get('from_52h', 0) < -10:
            score += 5
        m['score'] = score
        candidates.append(m)

    candidates.sort(key=lambda x: x['score'], reverse=True)

    # 5. Build report
    print('[5/5] Building report...\n')
    lines = []
    lines.append('<b>POST-MARKET INTELLIGENCE -- ' + report_date + '</b>\n')
    lines.append('<b>Market Mood: ' + market_mood + '</b>')
    lines.append('SPY: ' + format(spy_chg, '+.2f') + '% | QQQ: ' + format(idx_data.get('QQQ', 0), '+.2f') + '%\n')

    lines.append("<b>TODAY'S BIGGEST MOVERS:</b>")
    for m in movers[:12]:
        arrow = '+' if m['day_chg'] > 0 else ''
        lines.append('  ' + m['sym'].ljust(6) + ' $' + format(m['price'], '.2f') +
                     ' ' + format(m['day_chg'], '+.1f') + '%' +
                     ' H:$' + format(m['day_high'], '.2f') +
                     ' L:$' + format(m['day_low'], '.2f'))

    lines.append('\n<b>52-WEEK CONTEXT + AVG MOVE:</b>')
    for m in candidates[:8]:
        lines.append('  ' + m['sym'].ljust(6) + ' $' + format(m['price'], '.2f') +
                     ' | 52H:$' + format(m.get('wk52_high', 0), '.2f') +
                     ' 52L:$' + format(m.get('wk52_low', 0), '.2f') +
                     ' | AvgMove:' + format(m.get('avg_daily_move', 0), '.1f') + '%' +
                     ' | From52H:' + format(m.get('from_52h', 0), '+.1f') + '%')

    lines.append('\n<b>TOMORROW TRADE SUGGESTIONS:</b>')
    for i, m in enumerate(candidates[:5], 1):
        direction = 'LONG' if m['day_chg'] > 0 else 'SHORT'
        lines.append('  #' + str(i) + ' ' + direction + ' ' + m['sym'] +
                     ' (score:' + format(m['score'], '.1f') + ')' +
                     ' Today:' + format(m['day_chg'], '+.1f') + '%' +
                     ' AvgMove:' + format(m.get('avg_daily_move', 0), '.1f') + '%')

    if earnings_news:
        lines.append('\n<b>EARNINGS TONIGHT:</b>')
        for h in earnings_news[:6]:
            lines.append('  * ' + h[:100])

    lines.append('\n<b>KEY HEADLINES:</b>')
    for h in general_news[:6]:
        lines.append('  * ' + h[:100])

    report = '\n'.join(lines)
    print(report.replace('<b>', '').replace('</b>', ''))

    # Save
    report_file = REPORT_DIR / ('postmarket_' + report_date + '.json')
    report_file.write_text(json.dumps({
        'date': report_date, 'market_mood': market_mood,
        'idx': idx_data,
        'top_movers': [{k: v for k, v in m.items() if k != 'score'} for m in movers[:20]],
        'candidates': [{'sym': c['sym'], 'score': c['score'], 'day_chg': c['day_chg'],
                        'wk52_high': c.get('wk52_high', 0), 'wk52_low': c.get('wk52_low', 0),
                        'avg_daily_move': c.get('avg_daily_move', 0)} for c in candidates[:10]],
        'earnings_news': earnings_news[:10],
        'headlines': general_news[:15],
    }, indent=2))
    print('\nReport saved: ' + str(report_file))
    send_telegram(report)
    print('Telegram sent.')

if __name__ == '__main__':
    analyze_postmarket()
