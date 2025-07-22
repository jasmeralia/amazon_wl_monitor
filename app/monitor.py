import os
import re
import requests
from bs4 import BeautifulSoup
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import sys
import random

# ======= SETTINGS =======
WISHLISTS_RAW = os.getenv("WISHLISTS", "")
CACHE_FILE = "/data/wishlist_cache.json"
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.3"
)

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TO_ADDRESS = os.getenv("TO_ADDRESS")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "21600"))   # base seconds
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))              # base seconds between page requests
WISHLIST_SLEEP = int(os.getenv("WISHLIST_SLEEP", "60"))     # base seconds between wishlists
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "6000"))           # base seconds on full fetch failure
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "3"))            # retries on fetch failure
RETRY_SLEEP = int(os.getenv("RETRY_SLEEP", "600"))          # base seconds between retries
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "1200"))     # base seconds on captcha/block
# ========================

# Mobile wishlist URL template
MOBILE_LIST_URL = "https://www.amazon.com/gp/aw/ls?lid={}&ty=wishlist"

# Top 10 Mobile User-Agent strings
TOP_MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.3",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) GSA/360.1.737798518 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/134.0.6998.99 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/125.0.0.0 Mobile Safari/537.3",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1.1 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.3",
    "Mozilla/5.0 (Android 14; Mobile; rv:136.0) Gecko/136.0 Firefox/136.0"
]


def get_random_user_agent():
    return random.choice(TOP_MOBILE_USER_AGENTS)


def normalize_wishlist_url(url):
    m = re.search(r"/hz/wishlist/ls/([A-Za-z0-9]+)/?", url)
    if not m:
        m = re.search(r"/gp/registry/(?:wishlist|list)/([A-Za-z0-9]+)/?", url)
    if m:
        return MOBILE_LIST_URL.format(m.group(1))
    return url


def parse_wishlists(env_value):
    wishlists = []
    for entry in env_value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "|" in entry:
            name, url = entry.split("|", 1)
            wishlists.append({"name": name.strip(), "url": url.strip()})
        else:
            wishlists.append({"name": entry, "url": entry})
    return wishlists


def send_email(subject, body):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = TO_ADDRESS
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, TO_ADDRESS, msg.as_string())
        log(f"Notification sent: {subject}")
    except Exception as e:
        log(f"Failed to send email: {e}")


def fetch_wishlist_items(url, user_agent=None):
    session = requests.Session()
    next_url = normalize_wishlist_url(url)
    log(f"Using mobile URL: {next_url}")
    headers = {
        "User-Agent": user_agent or USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.amazon.com/"
    }
    items = []
    seen = set()
    page = 1

    try:
        while next_url:
            # Fetch page
            for attempt in range(1, RETRY_COUNT+1):
                try:
                    resp = session.get(next_url, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        break
                    log(f"Attempt {attempt}: HTTP {resp.status_code} for page {page}")
                except Exception as e:
                    log(f"Attempt {attempt}: Exception fetching page {page}: {e}")
                if attempt < RETRY_COUNT:
                    sd = random.uniform(RETRY_SLEEP*0.5, RETRY_SLEEP*1.5)
                    log(f"Sleeping {sd:.1f}s before retry {attempt+1}.")
                    time.sleep(sd)
                else:
                    sd = random.uniform(FAIL_SLEEP*0.5, FAIL_SLEEP*1.5)
                    log(f"Sleeping {sd:.1f}s after repeated failures.")
                    time.sleep(sd)
                    return None

            soup = BeautifulSoup(resp.text, "html.parser")
            li_items = soup.select("li[id^='itemWrapper_']")

            # CAPTCHA on first page
            if page == 1 and not li_items:
                text = resp.text.lower()
                sd = random.uniform(CAPTCHA_SLEEP*0.5, CAPTCHA_SLEEP*1.5)
                if "captcha" in text or "enter the characters you see" in text:
                    log(f"CAPTCHA detected; sleeping {sd:.1f}s before retry.")
                else:
                    log(f"Unexpected empty HTML; sleeping {sd:.1f}s.")
                time.sleep(sd)
                return None

            if not li_items:
                log(f"No items found on page {page}")
                break

            # Parse items on this page
            page_count = 0
            for li in li_items:
                link = li.select_one("a.a-touch-link-image[href]")
                href = link['href'].split('?')[0] if link else None
                full = href if href and href.startswith('http') else ("https://www.amazon.com"+href if href else None)
                title = li.select_one(".awl-item-title")
                name = title.get_text(strip=True) if title else None
                price = li.get('data-price') or (li.select_one("span.a-offscreen").get_text(strip=True) if li.select_one("span.a-offscreen") else None)
                key = full or name
                if key not in seen:
                    seen.add(key)
                    items.append({"name": name, "url": full, "price": price})
                    page_count += 1
                    log(f"Discovered new item: {name}")

            total = len(seen)
            log(f"Page {page}: found {page_count} new items (total {total})")

            # Find next pagination token
            token_input = soup.select_one("form.scroll-state input.showMoreUrl")
            if token_input and token_input.get('value'):
                next_url = "https://www.amazon.com" + token_input['value']
                page += 1
                sd = random.uniform(PAGE_SLEEP*0.5, PAGE_SLEEP*1.5)
                log(f"Sleeping {sd:.1f}s before next page (page {page})")
                time.sleep(sd)
            else:
                log("No further pages; pagination complete.")
                break

        return items

    except Exception as e:
        sd = random.uniform(FAIL_SLEEP*0.5, FAIL_SLEEP*1.5)
        log(f"Exception {e}; sleeping {sd:.1f}s.")
        time.sleep(sd)
        return None


def load_cache():
    if os.path.exists(CACHE_FILE):
        raw = json.load(open(CACHE_FILE))
        new = {}
        for u, itms in raw.items():
            cleaned = []
            for itm in itms:
                if isinstance(itm, dict):
                    cleaned.append({
                        'name': itm.get('name'),
                        'url': itm.get('url'),
                        'price': itm.get('price')
                    })
                else:
                    cleaned.append({'name': itm, 'url': None, 'price': None})
            new[u] = cleaned
        try:
            json.dump(new, open(CACHE_FILE, 'w'), indent=2)
        except:
            pass
        return new
    return {}


def save_cache(cache):
    json.dump(cache, open(CACHE_FILE, 'w'), indent=2)


def compare_items(old, new):
    o_map = { i.get('url') or i['name']: i for i in old }
    n_map = { i.get('url') or i['name']: i for i in new }
    added = [n_map[k] for k in set(n_map)-set(o_map)]
    removed = [o_map[k] for k in set(o_map)-set(n_map)]
    changed = []
    for k in set(o_map)&set(n_map):
        o, n = o_map[k], n_map[k]
        if o.get('price') and n.get('price') and o['price']!=n['price']:
            changed.append({'name': n['name'], 'url': n.get('url'), 'old_price': o['price'], 'new_price': n['price']})
    return added, removed, changed


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        open("/data/monitor.log","a").write(line+"\n")
    except:
        pass


def monitor():
    log("Starting wishlist monitor...")
    cache = load_cache()
    lists = parse_wishlists(WISHLISTS_RAW)
    while True:
        random.shuffle(lists)
        for wl in lists:
            name, url = wl['name'], wl['url']
            if not url: continue
            ua = get_random_user_agent()
            log(f"User-Agent for {name}: {ua}")
            log(f"Checking {name}: {url}")
            items = fetch_wishlist_items(url, ua)
            if items is None:
                log(f"Skipping {name}, fetch error.")
                continue
            a, r, c = compare_items(cache.get(url,[]), items)
            if a or r or c:
                body = f"Changes in '{name}': {url}\n\n"
                if a:
                    body+="✅ Added:\n"
                    for it in a:
                        urlt=it.get('url') or "URL not found"
                        body+=f"- {it['name']} | {it.get('price')} | {urlt}\n"
                if r:
                    body+="❌ Removed:\n"
                    for it in r:
                        urlt=it.get('url') or "URL not found"
                        body+=f"- {it['name']} | {urlt}\n"
                if c:
                    body+="🔄 Price changes:\n"
                    for ch in c:
                        urlt=ch.get('url') or "URL not found"
                        body+=f"- {ch['name']}: {ch['old_price']} -> {ch['new_price']} | {urlt}\n"
                send_email(f"Wishlist Update: {name}", body)
                cache[url] = items
            else:
                log("No changes detected.")
            sd = random.uniform(WISHLIST_SLEEP*0.5, WISHLIST_SLEEP*1.5)
            log(f"Sleeping {sd:.1f}s before next wishlist.")
            time.sleep(sd)
        save_cache(cache)
        sd = random.uniform(CHECK_INTERVAL*0.5, CHECK_INTERVAL*1.5)
        log(f"Sleeping {sd:.1f}s before next cycle.")
        time.sleep(sd)

if __name__ == "__main__":
    monitor()
