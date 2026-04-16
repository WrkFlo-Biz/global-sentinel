#!/usr/bin/env python3
"""
Global Sentinel — Pre-Market Intelligence Scanner
Runs daily at 8:00 AM ET (before 9:30 open)

1. Scans previous day's biggest movers, highs/lows across broad market
2. Checks premarket activity
3. Pulls 52-week highs/lows and avg daily moves
4. Predicts open direction
5. Generates ranked trade suggestions for max upside
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

def get_news(limit=30):
    data = api_get(DATA_BASE, '/v1beta1/news?limit=' + str(limit))
    return data.get('news', data) if isinstance(data, dict) else data

def analyze_market():
    now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    report_date = now_et.strftime('%Y-%m-%d')
    print('\n' + '='*60)
    print('  PRE-MARKET INTELLIGENCE SCAN -- ' + report_date + ' ' + now_et.strftime('%H:%M') + ' ET')
    print('='*60 + '\n')

    # 1. Get snapshots
    print('[1/4] Scanning broad market snapshots...')
    snaps = get_snapshots(UNIVERSE)

    movers = []
    for sym, data in snaps.items():
        try:
            trade = data.get('latestTrade', {})
            prev = data.get('prevDailyBar', {})
            price = trade.get('p', 0)
            prev_close = prev.get('c', 0)
            prev_high = prev.get('h', 0)
            prev_low = prev.get('l', 0)
            prev_vol = prev.get('v', 0)
            prev_open = prev.get('o', 0)
            if not prev_close or not price:
                continue
            prev_chg = (prev_close - prev_open) / prev_open * 100 if prev_open else 0
            premarket_chg = (price - prev_close) / prev_close * 100
            prev_range = (prev_high - prev_low) / prev_close * 100 if prev_close else 0
            movers.append({
                'sym': sym, 'price': price, 'prev_close': prev_close,
                'prev_chg': prev_chg, 'premarket_chg': premarket_chg,
                'prev_high': prev_high, 'prev_low': prev_low,
                'prev_range': prev_range, 'prev_vol': prev_vol,
            })
        except Exception:
            continue

    movers.sort(key=lambda x: abs(x['premarket_chg']), reverse=True)

    # 2. Get 52-week data for top 20
    print('[2/4] Pulling 52-week data for top movers...')
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

    # 3. News
    print('[3/4] Scanning headlines...')
    news = get_news(30)
    key_headlines = []
    for n in news:
        h = n.get('headline', '')
        syms = ','.join(n.get('symbols', []))
        key_headlines.append(syms + ': ' + h)

    # 4. Score and rank
    print('[4/4] Generating trade suggestions...\n')
    idx_sentiment = {}
    for m in movers:
        if m['sym'] in ['SPY', 'QQQ', 'IWM', 'DIA']:
            idx_sentiment[m['sym']] = m['premarket_chg']

    spy_pm = idx_sentiment.get('SPY', 0)
    qqq_pm = idx_sentiment.get('QQQ', 0)
    market_call = 'BULLISH' if spy_pm > 0.5 else ('BEARISH' if spy_pm < -0.5 else 'NEUTRAL')

    candidates = []
    for m in top20:
        if not m.get('avg_daily_move'):
            continue
        score = 0
        score += m['premarket_chg'] * 3
        if m['prev_chg'] > 0 and m['premarket_chg'] > 0:
            score += 10
        if m.get('from_52l', 100) < 20:
            score += 5
        score += m.get('avg_daily_move', 0) * 2
        if m.get('from_52h', -100) > -5:
            score -= 5
        m['score'] = score
        candidates.append(m)

    candidates.sort(key=lambda x: x['score'], reverse=True)

    # Build Telegram report
    lines = []
    lines.append('<b>PRE-MARKET INTELLIGENCE -- ' + report_date + '</b>\n')
    lines.append('<b>Market Call: ' + market_call + '</b>')
    lines.append('SPY PM: ' + format(spy_pm, '+.2f') + '% | QQQ: ' + format(qqq_pm, '+.2f') + '%\n')

    lines.append('<b>TOP PREMARKET MOVERS:</b>')
    for m in movers[:10]:
        arrow = '+' if m['premarket_chg'] > 0 else ''
        lines.append('  ' + m['sym'].ljust(6) + ' $' + format(m['price'], '.2f') +
                     ' PM:' + format(m['premarket_chg'], '+.1f') + '%' +
                     ' Prev:' + format(m['prev_chg'], '+.1f') + '%')

    lines.append('\n<b>52-WEEK CONTEXT:</b>')
    for m in candidates[:8]:
        h52 = m.get('wk52_high', 0)
        l52 = m.get('wk52_low', 0)
        avg_mv = m.get('avg_daily_move', 0)
        lines.append('  ' + m['sym'].ljust(6) + ' $' + format(m['price'], '.2f') +
                     ' | 52H:$' + format(h52, '.2f') + ' 52L:$' + format(l52, '.2f') +
                     ' | AvgMove:' + format(avg_mv, '.1f') + '%')

    lines.append('\n<b>TRADE SUGGESTIONS (ranked):</b>')
    for i, m in enumerate(candidates[:5], 1):
        direction = 'LONG' if m['premarket_chg'] > 0 else 'SHORT'
        lines.append('  #' + str(i) + ' ' + direction + ' ' + m['sym'] +
                     ' (score:' + format(m['score'], '.1f') + ')' +
                     ' PM:' + format(m['premarket_chg'], '+.1f') + '%' +
                     ' AvgMove:' + format(m.get('avg_daily_move', 0), '.1f') + '%')

    lines.append('\n<b>KEY HEADLINES:</b>')
    for h in key_headlines[:8]:
        lines.append('  * ' + h[:100])

    report = '\n'.join(lines)
    print(report.replace('<b>', '').replace('</b>', ''))

    # Save
    report_file = REPORT_DIR / ('premarket_' + report_date + '.json')
    report_file.write_text(json.dumps({
        'date': report_date, 'market_call': market_call,
        'idx_sentiment': idx_sentiment,
        'top_movers': [{k: v for k, v in m.items() if k != 'score'} for m in movers[:20]],
        'candidates': [{'sym': c['sym'], 'score': c['score'], 'premarket_chg': c['premarket_chg'],
                        'wk52_high': c.get('wk52_high', 0), 'wk52_low': c.get('wk52_low', 0),
                        'avg_daily_move': c.get('avg_daily_move', 0)} for c in candidates[:10]],
        'headlines': key_headlines[:15],
    }, indent=2))
    print('\nReport saved: ' + str(report_file))
    send_telegram(report)
    print('Telegram sent.')

if __name__ == '__main__':
    analyze_market()
