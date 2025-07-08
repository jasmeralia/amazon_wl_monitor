# 📦 Amazon Wishlist Monitor

A lightweight Dockerized Python app that monitors one or more Amazon wishlists and sends email notifications (via Gmail) when items are **added** or **removed**.

## 🚀 Features
- Track multiple wishlists simultaneously
- Notifies you of **added/removed items**
- Runs as a Docker container with `docker-compose`
- Uses Gmail SMTP (App Password recommended)

---

## 🐳 Quick Start

### 1️⃣ Clone the Repository
```bash
git clone https://github.com/yourusername/wishlist-monitor.git
cd wishlist-monitor
```

### 2️⃣ Configure Environment Variables
Copy `.env.example` to `.env` and edit:
```bash
cp .env.example .env
```

- Add your wishlist URLs (comma-separated)
- Set your Gmail address and [App Password](https://support.google.com/accounts/answer/185833?hl=en)

### 3️⃣ Build and Run
```bash
docker-compose up --build -d
```

View logs:
```bash
docker-compose logs -f
```

## 📧 Gmail Setup
- Enable **2FA** on your Google account.
- Create an **App Password** for “Mail”.
- Use this App Password for `EMAIL_PASSWORD` in `.env`.

## 📝 License
MIT License.