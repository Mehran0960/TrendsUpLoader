#!/usr/bin/env python3
from __future__ import annotations

import html, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASES = ["https://api.memarketbot.ir/api", "http://api.memarketbot.ir/api"]
STATE = Path("affiliate_radar/state.json")

USER = os.environ["MEMARKET_USERNAME"].strip()
PASS = os.environ["MEMARKET_PASSWORD"].strip()
AFF = os.environ["MEMARKET_AFFILIATE_CODE"].strip()
BOT = os.environ["TELEGRAM_BOT_TOKEN"].strip()
CHAT = os.environ["TELEGRAM_CHAT_ID"].strip()

MIN_DISC = float(os.getenv("MIN_DISCOUNT", "30"))
DROP = float(os.getenv("PRICE_DROP_THRESHOLD", "8"))
LOW_STOCK = int(os.getenv("LOW_STOCK", "5"))
COOLDOWN = float(os.getenv("COOLDOWN_HOURS", "6")) * 3600
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "3"))
PER_PAGE = int(os.getenv("PER_PAGE", "200"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))
BOOTSTRAP_SILENT = os.getenv("BOOTSTRAP_SILENT", "false").lower() == "true"

for n, v in [("MEMARKET_USERNAME", USER), ("MEMARKET_PASSWORD", PASS),
             ("MEMARKET_AFFILIATE_CODE", AFF), ("TELEGRAM_BOT_TOKEN", BOT),
             ("TELEGRAM_CHAT_ID", CHAT)]:
    if not v:
        raise SystemExit(f"Missing required secret: {n}")

def request_text(url, params=None, data=None, timeout=30):
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    body = urlencode(data).encode() if data is not None else None
    req = Request(url, data=body, headers={
        "User-Agent": "MeMarketDealRadar/1.0",
        "Accept": "application/json,text/plain,*/*",
    }, method="POST" if data is not None else "GET")
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig", "replace")

def request_json(url, **kw):
    return json.loads(request_text(url, **kw))

def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)

def pick(obj, names):
    wanted = {x.lower() for x in names}
    for d in walk(obj):
        for k, v in d.items():
            if k.lower() in wanted and v not in (None, ""):
                return v
    return None

def as_num(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("٬", "").strip())
    except Exception:
        return default

def as_goods(obj):
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for d in walk(obj):
            for k, v in d.items():
                if k.lower() in {"data","goods","items","result","rows","products"} and isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
    return []

def login():
    err = None
    for base in BASES:
        try:
            p = request_json(f"{base}/users/Login", params={"u": USER, "p": PASS})
            token = pick(p, {"token","strToken","access_token","accessToken","authToken","jwt"})
            if isinstance(token, str) and token.strip():
                print(f"login=ok base={base}")
                return token.strip().strip('"')
            if isinstance(p, str) and p.strip():
                return p.strip().strip('"')
            raise RuntimeError(f"token not found: {str(p)[:250]}")
        except Exception as e:
            err = e
    raise RuntimeError(f"MeMarket login failed: {err}")

def get_goods(token):
    err = None
    for base in BASES:
        try:
            out = []
            for page in range(1, MAX_PAGES + 1):
                p = request_json(f"{base}/goods/getAllGoods",
                                 params={"page": page, "perpage": PER_PAGE, "token": token})
                goods = as_goods(p)
                print(f"page={page} count={len(goods)}")
                if not goods:
                    break
                out.extend(goods)
                if len(goods) < PER_PAGE:
                    break
            return out
        except Exception as e:
            err = e
    raise RuntimeError(f"MeMarket goods fetch failed: {err}")

def normalize(raw):
    pid = pick(raw, {"numApiGoodRef","numGoodRef","id","productId"})
    code = str(pick(raw, {"strGoodref","goodRef","code","productCode"}) or pid or "").strip()
    name = str(pick(raw, {"strGoodName","goodName","name","productName"}) or "").strip()
    regular = as_num(pick(raw, {"numGoodPrice","regularPrice","price"}))
    sale = as_num(pick(raw, {"numPriceWithDiscount","discountPrice","salePrice"}))
    stock = as_num(pick(raw, {"numStock","stock","quantity"}))
    images = str(pick(raw, {"strGoodImages","images","image"}) or "").strip()
    post = str(pick(raw, {"PostLink","purchaseLink","buyLink"}) or "").strip()
    if not code or not name or regular <= 0 or sale <= 0 or sale >= regular or stock <= 0:
        return None
    disc = (regular - sale) * 100 / regular
    if disc < MIN_DISC:
        return None
    image = next((x.strip() for x in reversed(images.split("^")) if x.strip()), "")
    pid_num = int(as_num(pid)) if as_num(pid) > 0 else 0
    purchase = post
    if pid_num:
        purchase = f"https://memarket24.ir/product/{pid_num}?s={AFF}"
    return {"code": code, "name": name, "regular": int(regular), "sale": int(sale),
            "stock": int(stock), "discount": round(disc,1),
            "image": image, "purchase": purchase}

def score(p, old):
    prev = as_num(old.get("sale")) if old else 0
    drop = ((prev - p["sale"]) / prev * 100) if prev > p["sale"] > 0 else 0
    score = 45 if p["discount"] >= 50 else 35 if p["discount"] >= 40 else 20
    reasons = [f"تخفیف {p['discount']:.0f}%"]
    if drop >= 15:
        score += 30; reasons.append(f"افت قیمت {drop:.0f}%")
    elif drop >= DROP:
        score += 20; reasons.append(f"افت قیمت {drop:.0f}%")
    if 0 < p["stock"] <= LOW_STOCK:
        score += 12; reasons.append(f"موجودی {p['stock']}")
    return score, reasons, drop

def send_telegram(p, reasons, drop, score):
    n = html.escape(p["name"])
    cap = [
        "🔥 <b>آفر داغ می‌مارکت</b>",
        f"<b>{n}</b>",
        "",
        f"💰 <s>{p['regular']:,}</s> → <b>{p['sale']:,} ریال</b>",
        f"🏷 تخفیف: <b>{p['discount']:.0f}%</b>",
        "📌 " + " • ".join(html.escape(x) for x in reasons),
    ]
    if drop >= DROP:
        cap.append(f"📉 افت قیمت مشاهده‌شده: <b>{drop:.1f}%</b>")
    if 0 < p["stock"] <= LOW_STOCK:
        cap.append(f"⚠️ موجودی: <b>{p['stock']}</b>")
    cap += [
        "",
        "⚠️ درصد تخفیف بر اساس قیمت مرجع خود می‌مارکت است؛ مقایسه مستقل بازار نیست.",
        f'🛒 <a href="{html.escape(p["purchase"], quote=True)}">مشاهده / خرید</a>',
        f"⭐ امتیاز رادار: {score}",
    ]
    text = "\n".join(cap)
    if p["image"]:
        try:
            request_json(f"https://api.telegram.org/bot{BOT}/sendPhoto",
                         data={"chat_id": CHAT, "photo": p["image"], "caption": text, "parse_mode": "HTML"})
            return
        except Exception as e:
            print(f"photo send failed: {e}")
    request_json(f"https://api.telegram.org/bot{BOT}/sendMessage",
                 data={"chat_id": CHAT, "text": text, "parse_mode": "HTML"})

def load_state():
    if not STATE.exists():
        return {"products": {}}
    try:
        x = json.loads(STATE.read_text(encoding="utf-8"))
        return x if isinstance(x, dict) and isinstance(x.get("products"), dict) else {"products": {}}
    except Exception:
        return {"products": {}}

def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def commit_state():
    s = subprocess.run(["git","status","--porcelain","--",str(STATE)], capture_output=True, text=True)
    if not s.stdout.strip():
        return
    subprocess.run(["git","config","user.name","github-actions[bot]"], check=True)
    subprocess.run(["git","config","user.email","41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git","add",str(STATE)], check=True)
    if subprocess.run(["git","diff","--cached","--quiet"], check=False).returncode == 0:
        return
    subprocess.run(["git","commit","-m","chore: update affiliate radar state [skip ci]"], check=True)
    subprocess.run(["git","push"], check=True)

def main():
    state = load_state()
    products = state["products"]
    token = login()
    goods = get_goods(token)
    candidates = []
    now = datetime.now(timezone.utc).timestamp()

    for raw in goods:
        p = normalize(raw)
        if not p:
            continue
        old = products.get(p["code"])
        sc, reasons, drop = score(p, old)
        last_alert = as_num(old.get("last_alert_ts")) if old else 0
        last_alert_sale = as_num(old.get("last_alert_sale")) if old else 0
        cooldown_ok = now - last_alert >= COOLDOWN
        materially_lower = last_alert_sale > 0 and p["sale"] <= last_alert_sale * (1 - DROP/100)
        trigger = (p["discount"] >= 40 or drop >= DROP or (p["discount"] >= MIN_DISC and p["stock"] <= LOW_STOCK))
        qualifies = trigger and sc >= 35 and (cooldown_ok or materially_lower)
        if BOOTSTRAP_SILENT and old is None:
            qualifies = False
        if qualifies:
            candidates.append((sc, p, reasons, drop))
        new = {"regular": p["regular"], "sale": p["sale"], "discount": p["discount"]}
        if old:
            for k in ("last_alert_ts","last_alert_sale"):
                if k in old:
                    new[k] = old[k]
        products[p["code"]] = new

    candidates.sort(key=lambda x: (x[0], x[1]["discount"], -x[1]["sale"]), reverse=True)
    sent = 0
    for sc, p, reasons, drop in candidates[:MAX_ALERTS]:
        try:
            send_telegram(p, reasons, drop, sc)
            products[p["code"]]["last_alert_ts"] = now
            products[p["code"]]["last_alert_sale"] = p["sale"]
            sent += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"telegram failed code={p['code']}: {e}", file=sys.stderr)

    save_state(state)
    commit_state()
    print(f"qualifying={len(candidates)} alerts_sent={sent}")

if __name__ == "__main__":
    main()
