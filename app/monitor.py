import os
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TO_ADDRESS = os.getenv("TO_ADDRESS")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "21600"))  # seconds
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))  # seconds between page requests
WISHLIST_SLEEP = int(os.getenv("WISHLIST_SLEEP", "60"))  # seconds between wishlists
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "6000"))  # seconds to sleep on full fetch failure
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "3"))  # Number of retries on fetch failure
RETRY_SLEEP = int(os.getenv("RETRY_SLEEP", "600"))  # Seconds to sleep between retries
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "1200"))  # seconds to sleep on captcha/block
# ========================

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
            wishlists.append({"name": entry.strip(), "url": entry.strip()})
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


def fetch_wishlist_items(url):
    headers = {"User-Agent": USER_AGENT}
    items = []
    page = 1

    try:
        while True:
            paged_url = url + ("&page=%d" % page if "?" in url else "?page=%d" % page)

            # Retry logic
            for attempt in range(1, RETRY_COUNT + 1):
                try:
                    response = requests.get(paged_url, headers=headers, timeout=15)
                    if response.status_code == 200:
                        break
                    else:
                        log(f"Attempt {attempt}: Failed to fetch page {page} for {url}: HTTP {response.status_code}")
                except Exception as e:
                    log(f"Attempt {attempt}: Exception fetching page {page} for {url}: {e}")
                if attempt < RETRY_COUNT:
                    log(f"Sleeping for {RETRY_SLEEP} seconds before retry...")
                    time.sleep(RETRY_SLEEP)
                else:
                    log(f"Sleeping for {FAIL_SLEEP} seconds due to repeated fetch failure.")
                    time.sleep(FAIL_SLEEP)
                    return None

            soup = BeautifulSoup(response.text, "html.parser")
            title_elems = soup.select("h2.a-size-base")

            # CAPTCHA or empty page detection
            if page == 1 and not title_elems:
                if "captcha" in response.text.lower() or "enter the characters you see below" in response.text.lower():
                    log(f"CAPTCHA or block detected on page 1 for {url}.")
                    log(f"Sleeping for {CAPTCHA_SLEEP} seconds due to CAPTCHA/block.")
                    time.sleep(CAPTCHA_SLEEP)
                else:
                    log(f"No items found on page 1 for {url}. HTML snippet: {response.text[:200]}")
                    log(f"Sleeping for {FAIL_SLEEP} seconds due to unexpected empty page.")
                    time.sleep(FAIL_SLEEP)
                return None

            if not title_elems:
                log(f"No items found on page {page} for {url}")
                break

            for title_elem in title_elems:
                name = title_elem.get_text(strip=True)
                # Attempt to find product link
                link_tag = title_elem.find_parent("a", class_="a-link-normal")
                product_url = None
                if link_tag and link_tag.get("href"):
                    href = link_tag["href"].split("?")[0]
                    product_url = href if href.startswith("http") else "https://www.amazon.com" + href
                # Attempt to find price
                price_elem = title_elem.find_next("span", class_="a-offscreen")
                price = price_elem.get_text(strip=True) if price_elem else None

                items.append({
                    "name": name,
                    "url": product_url,
                    "price": price
                })

            # Pagination
            next_button = soup.find("li", class_="a-last")
            if not next_button or "a-disabled" in next_button.get("class", []):
                break
            page += 1
            time.sleep(PAGE_SLEEP)

        # Deduplicate by URL or name
        unique = {}
        for item in items:
            key = item.get('url') or item['name']
            unique[key] = item
        return list(unique.values())

    except Exception as e:
        log(f"Exception while fetching wishlist {url}: {e}")
        log(f"Sleeping for {FAIL_SLEEP} seconds due to exception.")
        time.sleep(FAIL_SLEEP)
        return None


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def compare_items(old_items, new_items):
    # Map items by URL or name for diffing
    old_map = {item.get('url') or item['name']: item for item in old_items}
    new_map = {item.get('url') or item['name']: item for item in new_items}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    added_keys = new_keys - old_keys
    removed_keys = old_keys - new_keys

    added = [new_map[k] for k in added_keys]
    removed = [old_map[k] for k in removed_keys]

    # Detect price changes
    price_changed = []
    common_keys = old_keys & new_keys
    for k in common_keys:
        old_price = old_map[k].get('price')
        new_price = new_map[k].get('price')
        if old_price and new_price and old_price != new_price:
            price_changed.append({
                'name': new_map[k]['name'],
                'url': k,
                'old_price': old_price,
                'new_price': new_price
            })

    return added, removed, price_changed


def log(msg):
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    try:
        with open("/data/monitor.log", "a") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"Failed to write to /data/monitor.log: {e}", flush=True)


def monitor():
    log("Starting wishlist monitor...")
    cache = load_cache()

    while True:
        wishlists = WISHLISTS.copy()
        random.shuffle(wishlists)
        for wl in wishlists:
            name = wl["name"]
            url = wl["url"]
            if not url:
                continue

            log(f"Checking wishlist '{name}': {url}")
            new_items = fetch_wishlist_items(url)
            if new_items is None:
                log(f"Skipping '{name}' due to fetch error.")
                continue

            old_items = cache.get(url, [])
            added, removed, price_changed = compare_items(old_items, new_items)

            if added or removed or price_changed:
                body = f"Changes detected in wishlist '{name}': {url}\n\n"
                if added:
                    body += "✅ Added items with details:\n"
                    for item in added:
                        body += f"- {item['name']} | {item.get('price')} | {item.get('url')}\n"
                if removed:
                    body += "❌ Removed items:\n"
                    for item in removed:
                        body += f"- {item['name']} | {item.get('url')}\n"
                if price_changed:
                    body += "🔄 Price changes:\n"
                    for change in price_changed:
                        body += (
                            f"- {change['name']}: {change['old_price']} -> {change['new_price']} | {change['url']}\n"
                        )

                send_email(f"Amazon Wishlist Update: {name}", body)
                cache[url] = new_items
            else:
                log("No changes detected.")

            time.sleep(WISHLIST_SLEEP)

        save_cache(cache)
        log(f"Waiting {CHECK_INTERVAL} seconds before next check...")
        time.sleep(CHECK_INTERVAL)

WISHLISTS = parse_wishlists(WISHLISTS_RAW)

if __name__ == "__main__":
    monitor()
