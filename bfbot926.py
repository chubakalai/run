#!/usr/bin/env python3
"""
NLDCA v8

Usage:  python nldca_v8.py <COIN> <dollars> <band>
  e.g:  python nldca_v8.py BTC 5 0.1

CSV files (same directory, auto-selected by coin):
  BTC → hist.csv        timestamp,open,high,low,close   (Unix s/ms, comma-sep)
  ETH → eth.csv         Date,Open,High,Low,Close,...    (May 10 2026, $)
  SOL → sol2.csv        Date,Price,Volume,Market_cap    (ISO, price-only → synthetic OHLC)                                                    XRP → hist2.csv       Date,Open,High,Low,Close,...    (May 10 2026, $)

OLS4: log10(price) ~ log10(years_since_genesis)
  BTC: skip first 300 bars before fitting
  Others: all bars

Anchor: most recent closed daily bar whose LOW is inside OLS ±band.
  Resets only when a new daily bar appears inside the band.
  old_atl = min(daily low) since anchor.

Buy trigger (checked every minute):
  If no pending order:
    last closed daily low < old_atl AND below OLS band
    → place limit at that low, no timeout — held open until filled.
    Skipped orders (below min size) carry leftover USD to next trigger.
    No new ATL checked until order fills.
  Each minute while order pending: check if filled.

SVG (Coin.svg, atomic write):
  Top:    full history daily OHLC  — log10(price) vs log10(years)  [no trigger line, no buy dots]
  Bottom: last 240m of 1m candles — log10(price) vs log10(years)
"""

import csv, datetime, hashlib, hmac, json, logging, math, os, sys, time
import urllib.error, urllib.parse, urllib.request
from typing import Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
                                                                      UTC = datetime.timezone.utc

MEXC_KEY    = os.getenv("MEXC")
MEXC_SECRET = os.getenv("MEXCSECRET")
MEXC_BASE   = "https://api.mexc.co"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger()
specs: Dict[str, Dict] = {}

# ── coin config ───────────────────────────────────────────────────────────────
COIN_CFG = {
    "BTC": {"csv":"hist.csv",  "binance":"BTCUSDT", "mexc":"BTC_USDT",
            "genesis":datetime.datetime(2009, 1, 3,18,15, 5,tzinfo=UTC), "ols_skip":300},
    "ETH": {"csv":"eth.csv",   "binance":"ETHUSDT", "mexc":"ETH_USDT",
            "genesis":datetime.datetime(2015, 7,30,15,26,13,tzinfo=UTC), "ols_skip":0},
    "SOL": {"csv":"sol2.csv",  "binance":"SOLUSDT", "mexc":"SOL_USDT",
            "genesis":datetime.datetime(2020, 3,16, 9, 0, 0,tzinfo=UTC), "ols_skip":0},
    "XRP": {"csv":"hist2.csv", "binance":"XRPUSDT", "mexc":"XRP_USDT",
            "genesis":datetime.datetime(2012, 1, 2, 0, 0, 0,tzinfo=UTC), "ols_skip":0},
}
                                                                      LEVERAGE = 10
SYMBOL=""; SYM=""; GENESIS=None; BAND=0.10; AMT_USD=10.0; OLS_SKIP=0; WND=0

# ── http ──────────────────────────────────────────────────────────────────────
def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())

def _http(method, url, headers=None, data=None, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(sorted(params.items()))       req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:                        body = r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
    return json.loads(body) if body.strip() else {}

# ── mexc ──────────────────────────────────────────────────────────────────────
def mexc(method, endpoint, params=None, body=None):
    params = params or {}
    ts  = str(int(time.time() * 1000))
    sp  = ("&".join(f"{k}={v}" for k, v in sorted(params.items()))
           if method == "GET"
           else (json.dumps(body, separators=(",",":"), sort_keys=True) if body else ""))
    sig = hmac.new(MEXC_SECRET.encode(), (MEXC_KEY+ts+sp).encode(), hashlib.sha256).hexdigest()
    hdr = {"ApiKey":MEXC_KEY,"Request-Time":ts,"Signature":sig,
           "Content-Type":"application/json","Accept":"application/json"}
    raw = (json.dumps(body,separators=(",",":"),sort_keys=True).encode()
           if body and method not in ("GET","DELETE") else None)
    try:
        return _http(method, MEXC_BASE+endpoint, headers=hdr, data=raw,
                     params=params if method in ("GET","DELETE") else None)
    except Exception as e:
        log.error(f"mexc {method} {endpoint}: {e}"); return {}
                                                                      # ── specs / sizing ────────────────────────────────────────────────────────────
def load_specs():
    rows = mexc("GET","/api/v1/contract/detail").get("data") or []
    for c in rows:
        s=c["symbol"].upper(); vu=float(c.get("volUnit",1)); pu=float(c.get("priceUnit",0.5))
        cs=float(c.get("contractSize",vu)); raw=f"{vu:.10f}".rstrip("0")
        p=len(raw.split(".")[1]) if "." in raw else 0
        specs[s]={"p":p,"t":pu,"vu":vu,"cs":cs}
    log.info(f"specs: {len(specs)} contracts  {SYM}={specs.get(SYM)}")

def _tick(): return specs.get(SYM,{}).get("t",0.5)                    def _prec(): return specs.get(SYM,{}).get("p",0)

def _rfmt_price(v):
    t=_tick(); r=round(v/t)*t; s=f"{t:.10f}".rstrip("0")
    dec=len(s.split(".")[1]) if "." in s else 0; return f"{r:.{dec}f}"

def _rfmt_vol(v):
    p=_prec()
    if p>=0: return f"{round(v,p):.{p}f}"
    d=10**abs(p); return str(int(round(v/d)*d))

def _contracts(usd, mark):
    """Contracts purchasable for `usd` dollars at `mark` price."""
    cs=specs.get(SYM,{}).get("cs",1.0)
    return float(_rfmt_vol(max(0, usd / (cs * mark))))

def _mos(): return specs.get(SYM,{}).get("vu",1.0)

# ── orders ────────────────────────────────────────────────────────────────────
def _open_ids() -> set:
    data=mexc("GET","/api/v1/private/order/list/open_orders",
              params={"symbol":SYM,"page_num":1,"page_size":50}).get("data") or []
    if isinstance(data,dict): data=data.get("resultList",[])
    return {str(o.get("orderId",o.get("id",""))) for o in data}

def place_buy(limit_price, mark, usd_amount) -> Optional[str]:
    """Place limit order for `usd_amount` dollars. Returns order id or None.
    Returns 'SKIP' if size below minimum (caller should carry over amount)."""
    vol = _contracts(usd_amount, mark)
    if vol < _mos():
        log.warning(f"size {vol} < min {_mos()} (${usd_amount:.2f}) — skipped, carrying over")
        return "SKIP"
    body={"leverage":LEVERAGE,"openType":2,"positionMode":1,
          "price":_rfmt_price(limit_price),"side":1,"symbol":SYM,"type":1,"vol":_rfmt_vol(vol)}
    r=mexc("POST","/api/v1/private/order/create",body=body)
    if not r.get("success"):
        log.error(f"order rejected: {r}"); return None
    oid=str(r.get("data",""))
    log.info(f"  limit buy {_rfmt_vol(vol)} @ {_rfmt_price(limit_price)}  id={oid}  usd={usd_amount:.2f}")
    return oid

def is_filled(oid: str) -> bool:
    return oid not in _open_ids()

def get_mark() -> float:
    d=mexc("GET","/api/v1/contract/ticker",params={"symbol":SYM}).get("data") or {}
    return float(d.get("fairPrice",d.get("lastPrice",0)) or 0)

# ── binance candles ───────────────────────────────────────────────────────────
def _bbase(): return "https://api.binance.com/api/v3/klines"

def fetch_daily(start_ms, end_ms) -> List[Dict]:
    rows, cur = [], start_ms
    while cur < end_ms:
        batch=_get(f"{_bbase()}?symbol={SYMBOL}&interval=1d&startTime={cur}&endTime={end_ms}&limit=1000")
        if not batch: break
        rows.extend(batch); cur=batch[-1][0]+1                                if len(batch)<1000: break
    now=int(time.time()*1000)
    return [{"t":int(r[0]),"o":float(r[1]),"h":float(r[2]),"l":float(r[3]),"c":float(r[4])}
            for r in rows if int(r[0])+86_400_000<=now]

def fetch_current_daily() -> Dict:
    """Returns current daily bar (incomplete) with intraday low."""
    batch = _get(f"{_bbase()}?symbol={SYMBOL}&interval=1d&limit=2")
    raw = batch[-1]  # most recent bar - may be incomplete
    return {"t": int(raw[0]), "o": float(raw[1]), "h": float(raw[2]),
            "l": float(raw[3]), "c": float(raw[4])}

def fetch_last_closed_daily() -> Dict:
    batch=_get(f"{_bbase()}?symbol={SYMBOL}&interval=1d&limit=3")
    now=int(time.time()*1000)
    for raw in reversed(batch[:-1]):
        if int(raw[0])+86_400_000<=now:
            return {"t":int(raw[0]),"o":float(raw[1]),"h":float(raw[2]),
                    "l":float(raw[3]),"c":float(raw[4])}
    raw=batch[-2]
    return {"t":int(raw[0]),"o":float(raw[1]),"h":float(raw[2]),
            "l":float(raw[3]),"c":float(raw[4])}

def _fetch_candles(interval: str, start_ms: int, bar_ms: int) -> List[Dict]:
    """Generic closed-bar fetcher for any Binance interval."""
    now=int(time.time()*1000)
    rows, cur = [], start_ms
    while cur < now:
        batch=_get(f"{_bbase()}?symbol={SYMBOL}&interval={interval}"
                   f"&startTime={cur}&endTime={now}&limit=1000")
        if not batch: break
        rows.extend(batch); cur=batch[-1][0]+1
        if len(batch)<1000: break
    return [{"t":int(r[0]),"o":float(r[1]),"h":float(r[2]),"l":float(r[3]),"c":float(r[4])}
            for r in rows if int(r[0])+bar_ms<=now]
                                                                      def fetch_1h_last_month() -> List[Dict]:
    now=int(time.time()*1000)
    return _fetch_candles("1h", now - 30*24*3600*1000, 3600*1000)

def fetch_1m_last_1h() -> List[Dict]:
    now=int(time.time()*1000)
    return _fetch_candles("1m", now - 60*60*1000, 60*1000)

# ── CSV ───────────────────────────────────────────────────────────────────────
def _num(s): return float(s.strip().replace("$","").replace(",","").replace(">","").split()[0])

def _parse_dt(s) -> Optional[datetime.datetime]:                          s=s.strip().strip('"').rstrip(".")
    try:
        v=float(s); ts=v/1000 if v>1e10 else v
        return datetime.datetime.fromtimestamp(ts,tz=UTC)
    except ValueError: pass                                               for suffix in (" UTC+0"," UTC+00:00","Z"):
        if s.endswith(suffix): s=s[:-len(suffix)]; break
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%dT%H:%M:%S","%Y-%m-%d",
                "%B %d %Y","%B %d, %Y","%b %d %Y","%b %d, %Y","%m/%d/%Y"):
        try: return datetime.datetime.strptime(s,fmt).replace(tzinfo=UTC)
        except ValueError: pass
    return None
                                                                      def load_csv(path) -> List[Dict]:
    rows=[]
    with open(path,newline="") as f:
        reader=csv.reader(f)
        header=[c.strip().strip('"').lower() for c in next(reader)]
        has_ohlc="open" in header and "low" in header                         price_only="price" in header and "open" not in header
        for row in reader:
            if not row or not row[0].strip(): continue                            try:
                dt=_parse_dt(row[0])
                if dt is None: continue
                if has_ohlc:
                    o,h,l,c=_num(row[1]),_num(row[2]),_num(row[3]),_num(row[4])
                elif price_only:                                                          c=_num(row[1]); o=c*0.998; h=c*1.004; l=c*0.996
                else: continue
            except (ValueError,IndexError): continue
            if all(v>0 for v in (o,h,l,c)):                                           rows.append({"dt":dt,"t":int(dt.timestamp()*1000),"o":o,"h":h,"l":l,"c":c})
    return rows

# ── OLS4 ─────────────────────────────────────────────────────────────────────
def _years(ts_ms) -> float:
    dt=datetime.datetime.fromtimestamp(ts_ms/1000,tz=UTC)
    return (dt-GENESIS).total_seconds()/(365.25*24*3600)              
def _log_years(ts_ms) -> Optional[float]:                                 y=_years(ts_ms); return math.log10(y) if y>0 else None

def _ols(x,y) -> Tuple[float,float]:                                      n=len(x); sx=sum(x); sy=sum(y)
    sxy=sum(x[i]*y[i] for i in range(n)); sx2=sum(x[i]**2 for i in range(n))
    d=n*sx2-sx*sx                                                         if d==0: return 0.0,sy/n
    sl=(n*sxy-sx*sy)/d; return sl,(sy-sl*sx)/n

def fit_ols4(bars) -> Tuple[float,float]:
    cx=[]; cy=[]
    for b in bars[OLS_SKIP:]:
        lx=_log_years(b["t"])
        if lx is not None and b["c"]>0:
            cx.append(lx); cy.append(math.log10(b["c"]))
    sl,ic=0.0,0.0
    for _ in range(4):
        if len(cx)<2: break                                                   sl,ic=_ols(cx,cy)
        pred=[sl*x+ic for x in cx]
        order=sorted(range(len(cx)),key=lambda k:cy[k]-pred[k])
        keep=set(order[:max(1,len(order)//2)])
        cx=[cx[k] for k in range(len(cx)) if k in keep]                       cy=[cy[k] for k in range(len(cy)) if k in keep]
    return sl,ic

def ols_log_price(sl,ic,ts_ms) -> Optional[float]:
    lx=_log_years(ts_ms); return sl*lx+ic if lx is not None else None

def ols_centre(sl,ic,ts_ms) -> float:
    v=ols_log_price(sl,ic,ts_ms); return 10**v if v is not None else 0.0                                                                    
def in_band(sl,ic,ts_ms,price) -> bool:
    v=ols_log_price(sl,ic,ts_ms)
    return price>0 and v is not None and abs(math.log10(price)-v)<=BAND

def below_band(sl,ic,ts_ms,price) -> bool:
    v=ols_log_price(sl,ic,ts_ms)
    return price>0 and v is not None and math.log10(price)<v-BAND

def find_anchor(bars, sl, ic) -> Optional[Dict]:                          """Most recent band-entry crossover: bar[i] low in-band, bar[i-1] low out-of-band."""
    for i in range(len(bars)-1, 0, -1):
        if in_band(sl, ic, bars[i]["t"], bars[i]["l"]) and \
           not in_band(sl, ic, bars[i-1]["t"], bars[i-1]["l"]):
            return bars[i]                                                return None

# ── SVG ───────────────────────────────────────────────────────────────────────
def write_svg(all_bars, bars_1h, bars_1m, sl, ic, anchor_t, buys, coin,
              pending_price=None, trigger_price=None, cycle_info=None):                                                                         """
    Top panel:    1h candles, last 1 month        (full width)            Bottom-left:  1m candles, last 1h             (half width)
    Bottom-right: 1d candles, since genesis       (half width)            trigger line + buy dots: bottom panels only
    """                                                                   W=1200; H=760
    ML=82; MR=24; MT=22                                                   PH=290; GAP=30
    cW=W-ML-MR
    # bottom half-widths (split at midpoint with a small gap)
    BGAP=10                                                               HW=(cW-BGAP)//2   # each sub-panel pixel width

    # ── shared panel rendereshared panel renderer ────────────────────────────────────────────
    def _panel(bars, y0, x0, pw, label,                                              show_trigger=False, show_buys=False):
        """Render one OHLC panel. x0/pw allow side-by-side sub-panels."""
        if len(bars)<2: return []
        lx_vals=[_log_years(b["t"]) for b in bars]
        ly_c=[math.log10(b["c"]) if b["c"]>0 else None for b in bars]         ly_h=[math.log10(b["h"]) if b["h"]>0 else None for b in bars]
        ly_l=[math.log10(b["l"]) if b["l"]>0 else None for b in bars]
        ly_o=[math.log10(b["o"]) if b["o"]>0 else None for b in bars]
        ols_ly=[ols_log_price(sl,ic,b["t"]) for b in bars]
        bhi_ly=[v+BAND if v is not None else None for v in ols_ly]
        blo_ly=[v-BAND if v is not None else None for v in ols_ly]

        # Y bounds: price high/low only, with small fixed pixel padding (5px each side)
        vy=[v for v in ly_h+ly_l if v is not None]
        vx=[v for v in lx_vals if v is not None]
        if not vx or not vy: return []

        xmn=min(vx); xmx=max(vx); xrng=xmx-xmn or 1.0
        PAD=5  # pixels of padding top and bottom
        raw_ymn=min(vy); raw_ymx=max(vy); raw_yrng=raw_ymx-raw_ymn or 1e-6
        # convert PAD pixels → log units
        pad_log=PAD/PH*raw_yrng
        ymn=raw_ymn-pad_log; ymx=raw_ymx+pad_log; yrng=ymx-ymn
        x1=x0+pw
                                                                              def xp(lx): return x0+(lx-xmn)/xrng*pw if lx is not None else None
        def yp(ly): return y0+PH-(ly-ymn)/yrng*PH if ly is not None else None
        def yclip(y):                                                             if y is None: return None
            return max(y0, min(y0+PH, y))

        out=[]
        out.append(f'<rect x="{x0}" y="{y0}" width="{pw}" height="{PH}" '                                                                                      f'fill="#f8f9fa" stroke="#bbb" stroke-width="1"/>')
        out.append(f'<text x="{x0+6}" y="{y0+15}" font-family="Courier New" '
                   f'font-size="10" fill="#444">{label}</text>')      
        # price grid lines + labels (only on left edge of each panel)         for d in range(int(math.floor(ymn)), int(math.ceil(ymx))+1):
            y=yp(float(d))
            if y is not None and y0<=y<=y0+PH:
                out.append(f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" '
                           f'stroke="#ddd" stroke-width="0.6"/>')
                out.append(f'<text x="{x0-3}" y="{y+4:.1f}" text-anchor="end" '
                           f'font-family="Courier New" font-size="8" fill="#888">'
                           f'${10**d:,.0f}</text>')

        # year grid lines
        for yr in [0.01,0.1,0.5,1,2,5,10,15,20]:
            lx=math.log10(yr)
            if lx<xmn or lx>xmx: continue
            x=xp(lx)
            out.append(f'<line x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y0+PH}" '                                                                                  f'stroke="#e8e8e8" stroke-width="0.6"/>')
            out.append(f'<text x="{x:.1f}" y="{y0+PH+13}" text-anchor="middle" '                                                                                   f'font-family="Courier New" font-size="8" fill="#888">{yr}yr</text>')
                                                                              # band polygon — only render if band interval intersects price y range
        hi_pts=[(xp(lx_vals[i]), yp(bhi_ly[i])) for i in range(len(bars))                                                                                   if lx_vals[i] is not None and bhi_ly[i] is not None]
        lo_pts=[(xp(lx_vals[i]), yp(blo_ly[i])) for i in range(len(bars))
                if lx_vals[i] is not None and blo_ly[i] is not None]          # interval intersection: band [min(blo), max(bhi)] must overlap [ymn, ymx]
        bhi_vals=[v for v in bhi_ly if v is not None]
        blo_vals=[v for v in blo_ly if v is not None]
        band_overlaps=(bhi_vals and blo_vals and
                       max(bhi_vals) >= ymn and min(blo_vals) <= ymx)
        if band_overlaps:
            hi_v=[(x, max(y0, min(y0+PH, y))) for x,y in hi_pts if x is not None and y is not None]
            lo_v=[(x, max(y0, min(y0+PH, y))) for x,y in lo_pts if x is not None and y is not None]
            if hi_v and lo_v:
                poly=(" ".join(f"{x:.1f},{y:.1f}" for x,y in hi_v)+" "+
                      " ".join(f"{x:.1f},{y:.1f}" for x,y in reversed(lo_v)))
                out.append(f'<polygon points="{poly}" fill="#f5c518" opacity="0.10"/>')
                out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in hi_v)}" '
                           f'fill="none" stroke="#f5c518" stroke-width="1" '
                           f'stroke-dasharray="4,3"/>')
                out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in lo_v)}" '
                           f'fill="none" stroke="#f5c518" stroke-width="1" '
                           f'stroke-dasharray="4,3"/>')

        # OLS line — only render if it overlaps price y range
        ols_pts=[(xp(lx_vals[i]), yp(ols_ly[i])) for i in range(len(bars))
                 if lx_vals[i] is not None and ols_ly[i] is not None]
        ols_vals=[v for v in ols_ly if v is not None]
        ols_overlaps=(ols_vals and min(ols_vals) <= ymx and max(ols_vals) >= ymn)
        if ols_overlaps:
            ols_v=[(x, max(y0, min(y0+PH, y))) for x,y in ols_pts if x is not None and y is not None]
            out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in ols_v)}" '
                       f'fill="none" stroke="#e74c3c" stroke-width="2" opacity="0.85"/>')

        # anchor line
        if anchor_t:
            lx_a=_log_years(anchor_t)
            if lx_a is not None and xmn<=lx_a<=xmx:
                ax=xp(lx_a)
                if ax is not None:
                    out.append(f'<line x1="{ax:.1f}" y1="{y0+2}" x2="{ax:.1f}" y2="{y0+PH-2}" '
                               f'stroke="#999" stroke-width="1" stroke-dasharray="3,3"/>')

        # trigger line
        if show_trigger and trigger_price is not None:
            lyt=math.log10(trigger_price) if trigger_price>0 else None
            ty=yp(lyt)
            if ty is not None and y0<=ty<=y0+PH:
                out.append(f'<line x1="{x0}" x2="{x1}" y1="{ty:.1f}" y2="{ty:.1f}" '
                           f'stroke="#27ae60" stroke-width="1.2" stroke-dasharray="6,4"/>')
                out.append(f'<text x="{x1-4}" y="{ty-4:.1f}" text-anchor="end" '
                           f'font-family="Courier New" font-size="8" fill="#27ae60">'
                           f'ATL {trigger_price:,.4f}</text>')

        # pending line
        if pending_price is not None:
            lyp=math.log10(pending_price) if pending_price>0 else None
            py=yp(lyp)
            if py is not None and y0<=py<=y0+PH:
                out.append(f'<line x1="{x0}" x2="{x1}" y1="{py:.1f}" y2="{py:.1f}" '
                           f'stroke="#f5a623" stroke-width="1" stroke-dasharray="3,2"/>')
                out.append(f'<text x="{x1-4}" y="{py-3:.1f}" text-anchor="end" '
                           f'font-family="Courier New" font-size="8" fill="#f5a623">'
                           f'limit {pending_price:,.4f}</text>')

        # OHLC candles
        bw=max(1.0, pw/max(len(bars),1)*0.55)
        for i in range(len(bars)):
            x=xp(lx_vals[i]); yh=yp(ly_h[i]); yl=yp(ly_l[i])
            yo2=yp(ly_o[i]); yc=yp(ly_c[i])
            if any(v is None for v in (x,yh,yl,yo2,yc)): continue
            # clip to panel bounds
            if x<x0 or x>x1: continue
            clr="#1a8a1a" if bars[i]["c"]>=bars[i]["o"] else "#aa1111"
            out.append(f'<line x1="{x:.1f}" y1="{yclip(yh):.1f}" x2="{x:.1f}" '
                       f'y2="{yclip(yl):.1f}" stroke="{clr}" stroke-width="0.7"/>')
            top=min(yo2,yc); bot=max(yo2,yc,top+1)
            out.append(f'<rect x="{x-bw/2:.1f}" y="{yclip(top):.1f}" width="{bw:.1f}" '
                       f'height="{max(1,yclip(bot)-yclip(top)):.1f}" '
                       f'fill="{clr}" opacity="0.85"/>')

        # buy dots
        if show_buys:
            for ev in buys:
                lx_e=_log_years(ev["t"])
                ly_e=math.log10(ev["price"]) if ev["price"]>0 else None
                if lx_e is None or ly_e is None: continue
                bx=xp(lx_e); by=yp(ly_e)
                if bx is None or by is None: continue
                if bx<x0 or bx>x1: continue
                out.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="3" fill="#f5a623" '
                           f'stroke="#333" stroke-width="0.8"/>')
        return out

    # ── assemble SVG ─────────────────────────────────────────────────────
    now_str=datetime.datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    anc_str=(datetime.datetime.fromtimestamp(anchor_t/1000,tz=UTC).strftime("%Y-%m-%d")                                                                  if anchor_t else "none")
    pending_str=f"  PENDING@{pending_price:,.4f}" if pending_price else ""
    if cycle_info:
        ci=cycle_info
        mark=ci["price"]
        new_atl_mark="✓" if ci.get("new_atl") else "×"
        cycle_str=(f"  {ci['symbol']}  price={mark:,.4f}"
                   f"  ATL={ci['atl']:,.4f}"
                   f"  newATL={new_atl_mark}"
                   f"  newATL#={ci['new_atl_count']}")
    else:
        cycle_str=""

    svg=[
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="100%" style="max-width:{W}px;display:block">',
        f'<rect width="{W}" height="{H}" fill="#fafafa"/>',
        f'<text x="{W//2}" y="16" text-anchor="middle" font-family="Courier New" '
        f'font-size="11" fill="#333" font-weight="bold">'
        f'{coin} NLDCA  {now_str}  buys={len(buys)}{pending_str}{cycle_str}</text>',
    ]
                                                                          y_top = MT+6
    y_bot = MT+6+PH+GAP

    # Top: 1h last month (full width, OLS band bounds)
    svg+=_panel(bars_1h, y_top, ML, cW,
                f"1h — last 30d  OLS4 band=±{BAND*100:.0f}%",
                show_trigger=False, show_buys=False)

    # Bottom-left: 1m last 1h (trigger + buys)
    svg+=_panel(bars_1m, y_bot, ML, HW,
                f"1m — last 1h  anchor={anc_str}",
                show_trigger=True, show_buys=True)

    # Bottom-right: 1d since genesis (OLS band bounds, no trigger/buys)
    svg+=_panel(all_bars, y_bot, ML+HW+BGAP, HW,
                f"1d — since genesis  skip={OLS_SKIP}",
                show_trigger=False, show_buys=False)

    # legend
    lx2=ML; ly2=H-14
    for lc,ld,lt in [("#1a8a1a","none","up"),("#aa1111","none","down"),                                                                                          ("#e74c3c","none","OLS"),("#f5c518","4,3","±band"),
                     ("#999","3,3","anchor"),("#f5a623","3,2","limit"),
                     ("#27ae60","6,4","ATL")]:
        dash=f' stroke-dasharray="{ld}"' if ld!="none" else ""
        svg.append(f'<line x1="{lx2}" x2="{lx2+14}" y1="{ly2}" y2="{ly2}" '
                   f'stroke="{lc}" stroke-width="1.5"{dash}/>')
        svg.append(f'<text x="{lx2+17}" y="{ly2+4}" font-family="Courier New" '
                   f'font-size="8" fill="{lc}">{lt}</text>')
        lx2+=72
    svg.append(f'<circle cx="{lx2+4}" cy="{ly2}" r="3" fill="#f5a623" '                                                                                    f'stroke="#333" stroke-width="0.8"/>')
    svg.append(f'<text x="{lx2+11}" y="{ly2+4}" font-family="Courier New" '
               f'font-size="8" fill="#333">buy</text>')
    svg.append("</svg>")

    fname=f"{coin}.svg"; tmp=f"{fname}.{os.getpid()}.tmp"
    with open(tmp,"w") as f: f.write("\n".join(svg))
    os.replace(tmp,fname)

# ── helpers ───────────────────────────────────────────────────────────────────
def _date(ts_ms) -> datetime.date:
    return datetime.datetime.fromtimestamp(ts_ms/1000,tz=UTC).date()

import argparse

def main():
    global SYMBOL,SYM,GENESIS,BAND,AMT_USD,OLS_SKIP,LEVERAGE,WND

    parser = argparse.ArgumentParser(description='NLDCA v8')
    parser.add_argument('coin', type=str, help='Coin symbol (BTC, ETH, SOL, XRP)')
    parser.add_argument('dollars', type=float, help='Amount in USD per trigger')
    parser.add_argument('band', type=float, help='Band width (e.g. 0.1 for ±10%)')
    parser.add_argument('--lev', type=int, default=10, help='Leverage (default: 10)')
    parser.add_argument('--wnd', type=int, default=0, help='ATL lookback window in days (0=since band entry)')
    args = parser.parse_args()

    coin = args.coin.upper()
    AMT_USD = args.dollars
    BAND = args.band
    LEVERAGE = args.lev
    WND = args.wnd

    if coin not in COIN_CFG:
        log.error(f"unknown coin {coin}  supported: {list(COIN_CFG)}"); sys.exit(1)

    cfg=COIN_CFG[coin]; SYMBOL=cfg["binance"]; SYM=cfg["mexc"]
    GENESIS=cfg["genesis"]; OLS_SKIP=cfg["ols_skip"]

    if not MEXC_KEY or not MEXC_SECRET:
        log.error("MEXC / MEXCSECRET not set"); sys.exit(1)

    load_specs()
    if SYM not in specs:
        log.error(f"{SYM} not in MEXC specs"); sys.exit(1)

    csv_path=cfg["csv"]
    if not os.path.exists(csv_path):
        log.error(f"CSV not found: {csv_path}"); sys.exit(1)
    csv_bars=load_csv(csv_path)
    log.info(f"CSV {csv_path}: {len(csv_bars)} bars")

    now_ms=int(time.time()*1000)
    start_ms=csv_bars[0]["t"] if csv_bars else int(GENESIS.timestamp()*1000)
    live_bars=fetch_daily(start_ms,now_ms)
    log.info(f"Binance daily: {len(live_bars)} bars")

    by_day: Dict[datetime.date,Dict]={}
    for b in csv_bars:  by_day[_date(b["t"])]=b
    for b in live_bars: by_day[_date(b["t"])]=b
    all_bars=sorted(by_day.values(),key=lambda b:b["t"])
    log.info(f"merged: {len(all_bars)} bars  {_date(all_bars[0]['t'])} → {_date(all_bars[-1]['t'])}")

    sl,ic=fit_ols4(all_bars)
    log.info(f"OLS4  slope={sl:.4f}  intercept={ic:.4f}  skip={OLS_SKIP}")

    def _atl_since(bars, anchor_t):
        """Min low of all bars from anchor_t onward, or last WND days if set."""
        if WND > 0:
            cutoff = int(time.time()*1000) - WND * 86_400_000
            lookback = [b for b in bars if b["t"] >= cutoff]
        else:
            lookback = [b for b in bars if b["t"] >= anchor_t]
        return min(b["l"] for b in lookback) if lookback else None

    anchor = find_anchor(all_bars, sl, ic)
    if anchor is None:
        log.error(f"no band-entry crossover found — try wider band"); sys.exit(1)
    anchor_t = anchor["t"]
    old_atl = _atl_since(all_bars, anchor_t)
    wnd_str = f"{WND}d" if WND > 0 else "since-entry"
    log.info(f"anchor={_date(anchor_t)}  old_atl={old_atl:.4f}  lookback={wnd_str}")
                                                                          buys: List[Dict]=[]
    pending_oid: Optional[str]=None
    pending_price: Optional[float]=None

    # ── carry-over amount tracker ─────────────────────────────────────────
    # Accumulates leftover USD when an order is skipped (below min size).
    # On fill, resets to AMT_USD. On skip, adds AMT_USD for next trigger.
    carry_usd: float = AMT_USD
                                                                          new_atl_count: int = 0   # total new-ATL triggers this session
                                                                          bars_1h=fetch_1h_last_month()
    bars_1m=fetch_1m_last_1h()
    write_svg(all_bars, bars_1h, bars_1m, sl, ic, anchor_t, buys, coin, trigger_price=old_atl)                                              
    last_bar_date=_date(all_bars[-1]["t"])                                cycle=0
                                                                          while True:
        now=time.time(); wake=(int(now)//60+1)*60+1
        time.sleep(max(0,wake-now))
        cycle+=1
        try:
            now_ms=int(time.time()*1000)
            bars_1h=fetch_1h_last_month()                                         bars_1m=fetch_1m_last_1h()

            # ── if order pending, check fill ──────────────────────────────────
            if pending_oid is not None:
                if is_filled(pending_oid):
                    log.info(f"[{cycle}] filled id={pending_oid} @ {pending_price:.4f}")
                    buys.append({"t":now_ms,"price":pending_price})
                    old_atl=pending_price
                    pending_oid=None; pending_price=None
                    carry_usd = AMT_USD
                else:
                    mark = get_mark()
                    log.info(f"[{cycle}] waiting for fill  id={pending_oid} @ {pending_price:.4f}  mark={mark:.4f}")
                    write_svg(all_bars, bars_1h, bars_1m, sl, ic, anchor_t, buys, coin,
                             pending_price=pending_price, trigger_price=old_atl,
                             cycle_info={"symbol":SYM,"price":mark,"atl":old_atl,                                                                                                    "new_atl":False,"new_atl_count":new_atl_count})                                                                        continue
                                                                                  # ── normal cycle ──────────────────────────────────────────────────                                                                        bar = fetch_current_daily()
            low = bar["l"]                                                        bar_date = _date(bar["t"])
            ols_c = ols_centre(sl, ic, now_ms)
            mark = get_mark()
            new_day = bar_date > last_bar_date
            is_new_atl = False

            if new_day:
                last_bar_date = bar_date                                              by_day[bar_date] = bar
                all_bars = sorted(by_day.values(), key=lambda b: b["t"])                                                                                    # Re-detect anchor on each new day (crossover may have shifted)
                new_anchor = find_anchor(all_bars, sl, ic)                            if new_anchor and new_anchor["t"] != anchor_t:
                    anchor_t = new_anchor["t"]                                            old_atl = _atl_since(all_bars, anchor_t)
                    log.info(f"[{cycle}] new anchor crossover  {_date(anchor_t)}  atl={old_atl:.4f}")                                       
            # Check trigger
            if low < old_atl:
                is_new_atl = True                                                     new_atl_count += 1                                                    log.info(f"[{cycle}] {SYM}  price={mark:.4f}  ATL={low:.4f}  newATL=✓  newATL#={new_atl_count}"                                                      f"  carry_usd={carry_usd:.2f}")                              oid = place_buy(low, mark, carry_usd)                                 if oid == "SKIP":
                    # Below minimum — accumulate carry for next trigger
                    carry_usd += AMT_USD                                                  log.info(f"[{cycle}] carry_usd now {carry_usd:.2f}")
                elif oid is not None:                                                     pending_oid = oid
                    pending_price = low
                    # carry_usd stays until fill confirms; reset on fill
                # Update ATL regardless of order outcome                              old_atl = low
            else:                                                                     log.info(f"[{cycle}] {SYM}  price={mark:.4f}  ATL={old_atl:.4f}  newATL=×  newATL#={new_atl_count}")                        
            write_svg(all_bars, bars_1h, bars_1m, sl, ic, anchor_t, buys, coin,
                     pending_price=pending_price, trigger_price=old_atl,
                     cycle_info={"symbol":SYM,"price":mark,"atl":old_atl,                                                                                                    "new_atl":is_new_atl,"new_atl_count":new_atl_count})

        except Exception as e:
            log.warning(f"[{cycle}] {e}", exc_info=True)                                                                                    if __name__=="__main__":                                                  main()
