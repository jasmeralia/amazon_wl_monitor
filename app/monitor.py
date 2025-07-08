import os
import requests
from bs4 import BeautifulSoup
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time

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
        print(f"Notification sent: {subject}")
    except Exception as e:
        print(f"Failed to send email: {e}")

def fetch_wishlist_items(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    items = soup.find_all("h2", class_="a-size-base")
    item_titles = [item.get_text(strip=True) for item in items]
    return sorted(item_titles)

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

def monitor():
    print("Starting wishlist monitor...")
    cache = load_cache()

    while True:
        for wl in WISHLISTS:
            name = wl["name"]
            url = wl["url"]
            if not url:
                continue

            print(f"Checking wishlist '{name}': {url}")
            try:
                new_items = fetch_wishlist_items(url)
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
                    print("No changes detected.")

            except Exception as e:
                print(f"Error checking '{name}' ({url}): {e}")

        save_cache(cache)
        print(f"Waiting {CHECK_INTERVAL} seconds before next check...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    monitor()