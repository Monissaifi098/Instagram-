# 📸 Instagram Pro Automation Bot

## ✨ Naye Features (v2.0)

| Feature | Detail |
|---------|--------|
| 🔐 Persistent Login | Ek baar login karo — session save hota hai. Bot restart pe bhi logged in rahoge |
| 📧 2FA / Email OTP | Gmail ya SMS OTP code Telegram me bhejo, bot khud verify karega |
| 🔴 Live Status | Real-time login status, followers, following live dekho |
| ➕ Follow/Unfollow | Kisi bhi user ko bot se follow/unfollow karo |
| 🔍 User Search | Username search karo — full profile + relationship status |
| 👥 Followers List | Apne followers/following dekho |
| 🎞️ Animations | Login, logout, upload — sab pe smooth animations |
| 🚪 Logout Button | Proper logout with session clear |
| 📊 Full Analytics | Posts, stories, follows, unfollows sab track hota hai |

---

## ⚡ Setup (5 minutes)

### 1. Files Download Karo
```
bot.py
requirements.txt
.env.example
```

### 2. Install
```bash
pip install -r requirements.txt
```

### 3. .env Banao
```bash
cp .env.example .env
```
Fill karo:
```
TELEGRAM_TOKEN=your_token
ANTHROPIC_API_KEY=your_key
ALLOWED_USERS=your_telegram_id
```
> Instagram credentials ab BOT ke andar se enter karte hain (secure!)

### 4. Start Karo
```bash
python bot.py
```

---

## 📱 Commands

| Command | Kaam |
|---------|------|
| `/start` | Main menu with buttons |
| `/login` | Instagram login (username + password bot me) |
| `/logout` | Logout + session clear |
| `/live` | Live status panel |
| `/post` | Image upload karke post karo |
| `/genpost` | AI se post + hashtags banao |
| `/story` | Text se story banao & post karo |
| `/follow` | User follow karo |
| `/unfollow` | User unfollow karo |
| `/search` | User search karo |
| `/hashtags [topic]` | Hashtag suggestions |
| `/analytics` | Full stats |
| `/schedule` | Post schedule karo |
| `/cancel` | Current operation cancel |

---

## 🔐 Login Flow

```
/login
  → Username bhejo
  → Password bhejo (auto delete for security)
  
  Normal Login? → ✅ Done! Session saved.
  2FA Required? → 6-digit code bhejo → ✅ Done!
  Email OTP?    → Gmail/SMS code bhejo → ✅ Done!
```

Session `ig_session.pkl` me save hota hai.
Bot restart karo — automatically logged in rahoge!

---

## ⚠️ Tips

- Password message bot automatically delete karta hai security ke liye
- 2FA ko band mat karo — bot support karta hai
- Zyada fast follow/unfollow mat karo (Instagram ban kar sakta hai)
- Session file ko share mat karo kisi se
