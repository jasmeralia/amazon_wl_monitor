import os
import requests
from bs4 import BeautifulSoup
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import sys

# ======= SETTINGS =======
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

WISHLISTS = parse_wishlists(os.getenv("WISHLISTS", ""))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "21600"))  # seconds
CACHE_FILE = "/data/wishlist_cache.json"

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
TO_ADDRESS = os.getenv("TO_ADDRESS")
# ========================

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
    headers = {"User-Agent": "Mozilla/5.0"}
    items = []
    page = 1
    try:
        while True:
            paged_url = url
            if "?" in url:
                paged_url += f"&page={page}"
            else:
                paged_url += f"?page={page}"
            response = requests.get(paged_url, headers=headers, timeout=15)
            if response.status_code != 200:
                log(f"Failed to fetch page {page} for {url}: HTTP {response.status_code}")
                return None  # treat as fetch failure
            soup = BeautifulSoup(response.text, "html.parser")
            page_items = soup.find_all("h2", class_="a-size-base")
            # Detect possible CAPTCHA or block page
            if page == 1 and not page_items:
                if "captcha" in response.text.lower() or "Enter the characters you see below" in response.text:
                    log(f"CAPTCHA or block detected on page 1 for {url}.")
                else:
                    log(f"No items found on page 1 for {url}. Response start: {response.text[:200]}")
                return None  # treat as fetch failure
            if not page_items:
                log(f"No items found on page {page} for {url}")
                break
            items.extend(item.get_text(strip=True) for item in page_items)
            next_button = soup.find("li", class_="a-last")
            if not next_button or "a-disabled" in next_button.get("class", []):
                break
            page += 1
            time.sleep(1)  # polite delay between page requests
        return sorted(set(items))
    except Exception as e:
        log(f"Exception while fetching wishlist {url}: {e}")
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
    added = [item for item in new_items if item not in old_items]
    removed = [item for item in old_items if item not in new_items]
    return added, removed

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
        for wl in WISHLISTS:
            name = wl["name"]
            url = wl["url"]
            if not url:
                continue

            log(f"Checking wishlist '{name}': {url}")
            try:
                new_items = fetch_wishlist_items(url)
                if new_items is None:
                    log(f"Skipping '{name}' due to fetch error.")
                    continue
                old_items = cache.get(url, [])

                added, removed = compare_items(old_items, new_items)

                if added or removed:
                    body = f"Changes detected in wishlist '{name}': {url}\n\n"
                    if added:
                        body += "✅ Added:\n" + "\n".join(f"- {item}" for item in added) + "\n"
                    if removed:
                        body += "❌ Removed:\n" + "\n".join(f"- {item}" for item in removed) + "\n"
                    send_email(f"Amazon Wishlist Update: {name}", body)
                    cache[url] = new_items
                else:
                    log("No changes detected.")

            except Exception as e:
                log(f"Error checking '{name}' ({url}): {e}")

            time.sleep(3)  # delay between wishlists

        save_cache(cache)
        log(f"Waiting {CHECK_INTERVAL} seconds before next check...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()