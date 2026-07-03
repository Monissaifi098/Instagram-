import os, asyncio, json, random, threading, pickle, time
from datetime import datetime
from pathlib import Path
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
from instagrapi import Client as InstaClient
from instagrapi.exceptions import TwoFactorRequired, ChallengeRequired, BadPassword, UserNotFound
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── CONFIG ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USERS  = list(map(int, os.getenv("ALLOWED_USERS","").split(","))) if os.getenv("ALLOWED_USERS") else []
SESSION_FILE   = "ig_session.pkl"
CREDS_FILE     = "ig_creds.json"
ANALYTICS_FILE = "analytics.json"
VERSION        = "v4.0 Ultra Premium"

# States
(ST_USER, ST_PASS, ST_2FA, ST_OTP,
 ST_IMAGE, ST_CAP, ST_STORY_TEXT, ST_STORY_IMG,
 ST_REEL, ST_FOLLOW, ST_UNFOLLOW, ST_SEARCH,
 ST_LIKE, ST_COMMENT, ST_SCHED_TIME,
 ST_BULK_FOLLOW, ST_BULK_UNFOLLOW, ST_DM_USER,
 ST_DM_MSG, ST_BIO_TEXT, ST_HASHTAG_TOPIC,
 ST_CLOSE_FRIENDS, ST_HIGHLIGHTS) = range(23)

# ── GLOBALS ─────────────────────────────────────────────────────────────────
ig_client       = None
ig_logged_in    = False
ig_username     = ""
scheduled_posts = []
analytics_data  = {
    "posts":[],"stories":[],"reels":[],"follows":[],"unfollows":[],
    "likes":[],"comments":[],"dms":[],"bulk_follows":[]
}
SPIN = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

# ── ANIMATIONS ──────────────────────────────────────────────────────────────
async def anim_spin(msg, label, steps=7):
    for i in range(steps):
        try: await msg.edit_text(f"{SPIN[i%10]} {label}{'.'*(i%4)}")
        except: pass
        await asyncio.sleep(0.38)

async def anim_progress(msg, label, steps=8):
    for i in range(steps):
        bar = "█"*i + "░"*(steps-i)
        pct = int((i/steps)*100)
        try: await msg.edit_text(f"⚡ {label}\n\n[{bar}] {pct}%")
        except: pass
        await asyncio.sleep(0.35)
    try: await msg.edit_text(f"✅ {label}\n\n[████████] 100%")
    except: pass

async def anim_login(msg):
    frames = [
        "🔐 Connecting to Instagram",
        "🔐 Connecting to Instagram •",
        "📡 Establishing secure tunnel ••",
        "🔑 Verifying credentials •••",
        "🛡️ Security handshake ••",
        "✨ Almost there •",
        "🚀 Logging in...",
    ]
    for f in frames:
        try: await msg.edit_text(f)
        except: pass
        await asyncio.sleep(0.42)

async def anim_ok(msg, txt):
    for s in ["⚡","⚡⚡","✨✨✨", f"✅  {txt}"]:
        try: await msg.edit_text(s)
        except: pass
        await asyncio.sleep(0.25)

async def anim_logout(msg):
    for s in ["🔓 Disconnecting","🗑️ Clearing session •","🔒 Securing data ••","👋 See you soon •••","✅ Logged out!"]:
        try: await msg.edit_text(s)
        except: pass
        await asyncio.sleep(0.42)

# ── SESSION ──────────────────────────────────────────────────────────────────
def is_auth(uid):
    return not ALLOWED_USERS or uid in ALLOWED_USERS

def save_session():
    if ig_client:
        with open(SESSION_FILE,"wb") as f: pickle.dump(ig_client.get_settings(),f)
        with open(CREDS_FILE,"w") as f:    json.dump({"username":ig_username},f)

def load_session():
    global ig_client, ig_logged_in, ig_username
    if not Path(SESSION_FILE).exists(): return False
    try:
        with open(SESSION_FILE,"rb") as f: settings=pickle.load(f)
        creds={}
        if Path(CREDS_FILE).exists():
            with open(CREDS_FILE) as f: creds=json.load(f)
        ig_client=InstaClient(); ig_client.set_settings(settings)
        ig_client.login(creds.get("username",""),"")
        ig_logged_in=True; ig_username=creds.get("username",""); return True
    except: return False

def do_logout():
    global ig_client,ig_logged_in,ig_username
    try:
        if ig_client: ig_client.logout()
    except: pass
    ig_client=None; ig_logged_in=False; ig_username=""
    for f in [SESSION_FILE,CREDS_FILE]:
        if Path(f).exists(): Path(f).unlink()

def save_analytics():
    with open(ANALYTICS_FILE,"w") as f: json.dump(analytics_data,f,indent=2,default=str)

def load_analytics():
    global analytics_data
    if Path(ANALYTICS_FILE).exists():
        with open(ANALYTICS_FILE) as f: analytics_data=json.load(f)

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────

# Persistent bottom reply keyboard (always visible below chat input)
def reply_kb(logged_in):
    if logged_in:
        return ReplyKeyboardMarkup([
            ["🏠 Menu",        "👤 Profile",    "📊 Stats"],
            ["📸 Post",        "📱 Story",      "🎬 Reel"],
            ["➕ Follow",      "❤️ Like",       "🔍 Search"],
            ["🔴 Live Status", "🧪 Live Test",  "⚙️ Settings"],
        ], resize_keyboard=True, persistent=True)
    else:
        return ReplyKeyboardMarkup([
            ["🔐 Login", "ℹ️ About"],
        ], resize_keyboard=True, persistent=True)

# Main inline menu (shown in message body)
def main_kb(logged_in):
    if logged_in:
        return InlineKeyboardMarkup([
            # ── Header row
            [InlineKeyboardButton("━━━━━ 👤 ACCOUNT ━━━━━", callback_data="noop")],
            [InlineKeyboardButton("👤 Profile",          callback_data="m_profile"),
             InlineKeyboardButton("🔴 Live Status",      callback_data="m_live"),
             InlineKeyboardButton("📊 Analytics",        callback_data="m_analytics")],
            [InlineKeyboardButton("👥 Followers",        callback_data="m_followers"),
             InlineKeyboardButton("➡️ Following",        callback_data="m_following"),
             InlineKeyboardButton("📸 My Posts",         callback_data="m_myposts")],

            # ── Content
            [InlineKeyboardButton("━━━━━ 📤 CONTENT ━━━━━", callback_data="noop")],
            [InlineKeyboardButton("📸 Post Photo",       callback_data="m_post"),
             InlineKeyboardButton("📱 Post Story",       callback_data="m_story"),
             InlineKeyboardButton("🎬 Post Reel",        callback_data="m_reel")],
            [InlineKeyboardButton("⏰ Schedule Post",    callback_data="m_sched"),
             InlineKeyboardButton("📅 Scheduled Queue",  callback_data="m_scheduled"),
             InlineKeyboardButton("✏️ Edit Bio",         callback_data="m_bio")],

            # ── Social Actions
            [InlineKeyboardButton("━━━━━ 🤝 SOCIAL ━━━━━", callback_data="noop")],
            [InlineKeyboardButton("➕ Follow",           callback_data="m_follow"),
             InlineKeyboardButton("➖ Unfollow",         callback_data="m_unfollow"),
             InlineKeyboardButton("🔍 Search User",      callback_data="m_search")],
            [InlineKeyboardButton("❤️ Auto Like",        callback_data="m_like"),
             InlineKeyboardButton("💬 Comment",          callback_data="m_comment"),
             InlineKeyboardButton("📩 Send DM",          callback_data="m_dm")],

            # ── Premium Tools
            [InlineKeyboardButton("━━━━ ⚡ PREMIUM ━━━━",  callback_data="noop")],
            [InlineKeyboardButton("⚡ Bulk Follow",      callback_data="m_bulk_follow"),
             InlineKeyboardButton("🗑️ Bulk Unfollow",   callback_data="m_bulk_unfollow"),
             InlineKeyboardButton("🏷️ Hashtags",        callback_data="m_hashtags")],
            [InlineKeyboardButton("🕵️ Ghost Check",     callback_data="m_ghost"),
             InlineKeyboardButton("📈 Growth Stats",     callback_data="m_growth"),
             InlineKeyboardButton("🔔 Notifications",    callback_data="m_notifs")],

            # ── System
            [InlineKeyboardButton("━━━━━ 🛠 SYSTEM ━━━━━",  callback_data="noop")],
            [InlineKeyboardButton("🧪 Live Test",        callback_data="m_livetest"),
             InlineKeyboardButton("⚙️ Settings",         callback_data="m_settings"),
             InlineKeyboardButton("📖 Help",             callback_data="m_help")],

            # ── Divider + Logout
            [InlineKeyboardButton("━━━━━━━━━━━━━━━━━━━━━━", callback_data="noop")],
            [InlineKeyboardButton("🚪  LOGOUT  🚪",     callback_data="m_logout_confirm")],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐  LOGIN TO INSTAGRAM  🔐", callback_data="m_login")],
            [InlineKeyboardButton("📖 Commands",  callback_data="m_help"),
             InlineKeyboardButton("ℹ️ About",     callback_data="m_about")],
        ])

def back_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("◀️ Back", callback_data="m_home"),
        InlineKeyboardButton("🏠 Menu", callback_data="m_home")
    ]])

def back_refresh_kb(refresh_cb):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=refresh_cb),
        InlineKeyboardButton("🏠 Menu",    callback_data="m_home")
    ]])

def action_kb(extra_rows=None):
    """Generic confirm/cancel keyboard with optional extra rows above."""
    rows = extra_rows or []
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="m_home")])
    return InlineKeyboardMarkup(rows)

# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied."); return
    name = update.effective_user.first_name or "User"
    now  = datetime.now().strftime("%I:%M %p · %d %b %Y")
    if ig_logged_in:
        txt = (
            f"╔══════════════════════════════╗\n"
            f"║   📸  INSTAGRAM PRO BOT  📸  ║\n"
            f"║        {VERSION:<20}║\n"
            f"╚══════════════════════════════╝\n\n"
            f"👋 Welcome back, {name}!\n"
            f"✅ Connected: @{ig_username}\n"
            f"🟢 Status: Online & Active\n"
            f"🕐 {now}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Choose an action below 👇\n"
            f"(or use bottom keyboard shortcuts)"
        )
    else:
        txt = (
            f"╔══════════════════════════════╗\n"
            f"║   📸  INSTAGRAM PRO BOT  📸  ║\n"
            f"║        {VERSION:<20}║\n"
            f"╚══════════════════════════════╝\n\n"
            f"👋 Hello, {name}!\n\n"
            f"🔐 Tap Login to connect Instagram\n"
            f"💾 Session auto-saves — no re-login\n"
            f"🛡️ 2FA & Email OTP supported\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Tap the button below to begin 👇"
        )
    await update.message.reply_text(txt,
        reply_markup=main_kb(ig_logged_in))
    # Also send/refresh persistent bottom keyboard
    await update.message.reply_text(
        "⌨️ Shortcuts active below 👇",
        reply_markup=reply_kb(ig_logged_in)
    )

# ── Reply keyboard shortcut handler ─────────────────────────────────────────
async def handle_reply_shortcut(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id): return
    txt = update.message.text.strip()
    mapping = {
        "🏠 Menu":        "home",
        "👤 Profile":     "profile",
        "📊 Stats":       "analytics",
        "📸 Post":        "post",
        "📱 Story":       "story",
        "🎬 Reel":        "reel",
        "➕ Follow":      "follow",
        "❤️ Like":        "like",
        "🔍 Search":      "search",
        "🔴 Live Status": "live",
        "🧪 Live Test":   "livetest",
        "⚙️ Settings":    "settings",
        "🔐 Login":       "login_cmd",
        "ℹ️ About":       "about",
    }
    key = mapping.get(txt)
    if not key: return

    if key == "home":
        name = update.effective_user.first_name or "User"
        await update.message.reply_text(
            f"📸 INSTAGRAM PRO BOT\n\n"
            f"👋 {name}\n"
            f"{'✅ @'+ig_username if ig_logged_in else '❌ Not logged in'}\n\n"
            f"Select an option 👇",
            reply_markup=main_kb(ig_logged_in)
        )
    elif key == "login_cmd":
        await update.message.reply_text("👉 Type /login to start:")
    elif key == "about":
        await update.message.reply_text(
            f"ℹ️ Instagram Pro Bot\n"
            f"🏷️ {VERSION}\n\n"
            f"All features controlled right from\n"
            f"Telegram — no app needed.",
            reply_markup=back_kb()
        )
    elif key in ("post","story","reel","follow","like","search"):
        cmd_map = {"post":cmd_post,"story":cmd_story,"reel":cmd_reel,
                   "follow":cmd_follow,"like":cmd_like,"search":cmd_search}
        await cmd_map[key](update, ctx)
    else:
        # Route to show_* functions by faking a simple message display
        info_map = {
            "profile":   lambda: _quick_reply(update, "👤 Tap Profile in menu for full details.", main_kb(ig_logged_in)),
            "analytics": lambda: _quick_reply(update, "📊 Tap Analytics in menu for full stats.",  main_kb(ig_logged_in)),
            "live":      lambda: _quick_reply(update, "🔴 Type /live for live status.",             main_kb(ig_logged_in)),
            "livetest":  lambda: _quick_reply(update, "🧪 Opening live test panel...",             main_kb(ig_logged_in)),
            "settings":  lambda: _quick_reply(update, "⚙️ Tap Settings in menu.",                  main_kb(ig_logged_in)),
        }
        if key in info_map:
            await info_map[key]()
        else:
            await update.message.reply_text("👇 Use menu below:", reply_markup=main_kb(ig_logged_in))

async def _quick_reply(update, text, kb):
    await update.message.reply_text(text, reply_markup=kb)

# ── LOGIN ─────────────────────────────────────────────────────────────────────
async def cmd_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id): return
    if ig_logged_in:
        await update.message.reply_text(f"✅ Already logged in @{ig_username}", reply_markup=main_kb(True))
        return ConversationHandler.END
    await update.message.reply_text(
        "🔐 Instagram Login\n\n"
        "━━━━━━━━━━━━━━\n"
        "📝 Step 1/2: Username bhejo\n"
        "(@ ke bina)"
    )
    return ST_USER

async def h_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["u"] = update.message.text.strip().lstrip("@")
    await update.message.reply_text(
        f"✅ Username: @{ctx.user_data['u']}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔑 Step 2/2: Password bhejo\n"
        f"_(Security ke liye delete ho jayega)_",
        parse_mode="Markdown"
    )
    return ST_PASS

async def h_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global ig_client, ig_logged_in, ig_username
    pw = update.message.text.strip()
    try: await update.message.delete()
    except: pass
    ctx.user_data["p"] = pw
    msg = await update.message.reply_text("🔐 Logging in...")
    await anim_login(msg)
    try:
        ig_client = InstaClient()
        ig_client.delay_range = [1, 3]
        ig_client.login(ctx.user_data["u"], pw)
        ig_logged_in = True
        ig_username  = ctx.user_data["u"]
        save_session()
        await anim_ok(msg, f"Login successful!")
        await asyncio.sleep(0.3)
        await msg.edit_text(
            f"🎉 Login Successful!\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ Account: @{ig_username}\n"
            f"💾 Session saved (auto-login next time)\n"
            f"🔒 Password deleted from chat\n\n"
            f"Ab kya karein? 👇",
            reply_markup=main_kb(True)
        )
        await update.message.reply_text("⌨️ Shortcuts updated!", reply_markup=reply_kb(True))
        return ConversationHandler.END
    except TwoFactorRequired:
        await msg.edit_text(
            "📲 2FA Required!\n\n"
            "━━━━━━━━━━━━━━\n"
            "Authenticator app ya SMS se\n"
            "6-digit code bhejo:"
        )
        return ST_2FA
    except ChallengeRequired:
        try:
            ig_client.challenge_resolve(ig_client.last_json)
            await msg.edit_text(
                "📧 Email/SMS Verification!\n\n"
                "━━━━━━━━━━━━━━\n"
                "Instagram ne verification code bheja.\n"
                "Woh OTP yahan bhejo:"
            )
            return ST_OTP
        except Exception as e:
            await msg.edit_text(f"❌ Challenge error: {e}\n\nDobara /login karo")
            return ConversationHandler.END
    except BadPassword:
        await msg.edit_text("❌ Password galat!\n\nDobara /login karo")
        return ConversationHandler.END
    except Exception as e:
        await msg.edit_text(f"❌ Login error:\n{str(e)[:200]}\n\nDobara /login karo")
        return ConversationHandler.END

async def h_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global ig_logged_in, ig_username
    code = update.message.text.strip().replace(" ","")
    msg  = await update.message.reply_text("🔑 2FA verify ho raha hai...")
    try:
        ig_client.login(ctx.user_data.get("u",""), ctx.user_data.get("p",""), verification_code=code)
        ig_logged_in = True; ig_username = ctx.user_data.get("u",""); save_session()
        await anim_ok(msg, "2FA verified!")
        await asyncio.sleep(0.3)
        await msg.edit_text(f"🎉 Login! ✅ @{ig_username}", reply_markup=main_kb(True))
        await update.message.reply_text("⌨️ Shortcuts updated!", reply_markup=reply_kb(True))
    except Exception as e:
        await msg.edit_text(f"❌ Code galat ya expired.\n{e}\n\nDobara /login karo")
    return ConversationHandler.END

async def h_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global ig_logged_in, ig_username
    otp = update.message.text.strip().replace(" ","")
    msg = await update.message.reply_text("📧 OTP verify ho raha hai...")
    try:
        ig_client.challenge_resolve(ig_client.last_json, otp)
        ig_logged_in = True; ig_username = ctx.user_data.get("u",""); save_session()
        await anim_ok(msg, "OTP verified!")
        await asyncio.sleep(0.3)
        await msg.edit_text(f"🎉 Login! ✅ @{ig_username}", reply_markup=main_kb(True))
        await update.message.reply_text("⌨️ Shortcuts updated!", reply_markup=reply_kb(True))
    except Exception as e:
        await msg.edit_text(f"❌ OTP galat.\n{e}\n\nDobara /login karo")
    return ConversationHandler.END

# ── LOGOUT ────────────────────────────────────────────────────────────────────
async def cmd_logout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("⚠️ Already logged out."); return
    await update.message.reply_text(
        f"🚪 Logout Confirm?\n\n@{ig_username} se disconnect hoge.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Haan, Logout",callback_data="do_logout"),
             InlineKeyboardButton("❌ Nahi",        callback_data="m_home")]
        ])
    )

# ── LIVE STATUS ───────────────────────────────────────────────────────────────
async def show_live(target, edit=False):
    if ig_logged_in and ig_client:
        try:
            u   = ig_client.account_info()
            now = datetime.now()
            txt = (
                f"╔══════════════════════════════╗\n"
                f"║       🔴  LIVE STATUS        ║\n"
                f"╚══════════════════════════════╝\n\n"
                f"🟢🟢🟢🟢🟢  ONLINE & ACTIVE\n\n"
                f"━━━━━ 👤 Account ━━━━━\n"
                f"@{u.username}\n"
                f"📛 {u.full_name or 'N/A'}\n"
                f"🆔 {ig_client.user_id}\n\n"
                f"━━━━━ 📊 Stats ━━━━━\n"
                f"👥  Followers:  {u.follower_count:,}\n"
                f"➡️  Following:  {u.following_count:,}\n"
                f"📸  Posts:      {u.media_count:,}\n\n"
                f"━━━━━ ℹ️ Info ━━━━━\n"
                f"🔒 Private: {'Yes' if u.is_private else 'No'}\n"
                f"✅ Verified: {'Yes' if u.is_verified else 'No'}\n\n"
                f"📝 {(u.biography or 'No bio')[:80]}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now.strftime('%I:%M:%S %p')}\n"
                f"📅 {now.strftime('%d %b %Y')}\n"
                f"✅ Bot: Connected & Running"
            )
        except Exception as e:
            txt = f"⚠️ Error fetching status: {e}"
    else:
        txt = (
            "╔══════════════════════════════╗\n"
            "║       🔴  LIVE STATUS        ║\n"
            "╚══════════════════════════════╝\n\n"
            "🔴🔴🔴🔴🔴  OFFLINE\n\n"
            "❌ Instagram connected nahi hai\n\n"
            "👉 Login button se connect karo"
        )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh",   callback_data="m_live"),
         InlineKeyboardButton("🧪 Live Test", callback_data="m_livetest"),
         InlineKeyboardButton("🏠 Menu",      callback_data="m_home")]
    ])
    if edit: await target.edit_message_text(txt, reply_markup=kb)
    else:    await target.reply_text(txt, reply_markup=kb)

async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id): return
    await show_live(update.message)

# ── LIVE TEST ─────────────────────────────────────────────────────────────────
async def show_live_test(query):
    msg = await query.edit_message_text("🧪 Running live tests...")
    results = []
    results.append("✅ Bot: Online & Running")
    await asyncio.sleep(0.3)
    try: await msg.edit_text("🧪 Testing Instagram connection...")
    except: pass
    if ig_logged_in and ig_client:
        try:
            ig_client.account_info()
            results.append("✅ Instagram: Connected")
        except: results.append("❌ Instagram: Connection Error")
    else:
        results.append("❌ Instagram: Not Logged In")
    await asyncio.sleep(0.3)
    try: await msg.edit_text("🧪 Testing session...")
    except: pass
    results.append("✅ Session: Saved" if Path(SESSION_FILE).exists() else "❌ Session: Not saved")
    await asyncio.sleep(0.3)
    try: await msg.edit_text("🧪 Testing analytics...")
    except: pass
    results.append("✅ Analytics: Working" if Path(ANALYTICS_FILE).exists() else "⚠️ Analytics: No data yet")
    await asyncio.sleep(0.3)
    try: await msg.edit_text("🧪 Testing scheduler...")
    except: pass
    pending = sum(1 for p in scheduled_posts if p.get("status")=="scheduled")
    results.append(f"✅ Scheduler: {pending} post(s) queued")

    passed = sum(1 for r in results if r.startswith("✅"))
    total  = len(results)
    bar    = "✅"*passed + "❌"*(total-passed)
    txt = (
        f"╔══════════════════════════════╗\n"
        f"║      🧪  LIVE TEST REPORT    ║\n"
        f"╚══════════════════════════════╝\n\n"
        f"Score: {passed}/{total}  {bar}\n\n"
        f"━━━━━━ Results ━━━━━━\n"
    )
    for r in results: txt += f"{r}\n"
    txt += f"\n━━━━━━━━━━━━━━━━━━━━\n"
    txt += f"🕐 {datetime.now().strftime('%I:%M:%S %p  %d %b %Y')}"
    await msg.edit_text(txt, reply_markup=back_refresh_kb("m_livetest"))

# ── PROFILE ───────────────────────────────────────────────────────────────────
async def show_profile(query):
    if not ig_logged_in: await query.edit_message_text("❌ /login karo pehle"); return
    msg = await query.edit_message_text("⠋ Loading profile...")
    try:
        u = ig_client.account_info()
        try:
            media  = ig_client.user_medias(ig_client.user_id, 5)
            last   = media[0].taken_at.strftime('%d %b %Y') if media else 'N/A'
            avg_l  = sum(m.like_count for m in media) // max(len(media),1)
            avg_c  = sum(m.comment_count for m in media) // max(len(media),1)
        except: last='N/A'; avg_l=0; avg_c=0
        er = f"{((avg_l+avg_c)/max(u.follower_count,1))*100:.2f}%" if u.follower_count else "N/A"
        txt = (
            f"╔══════════════════════════════╗\n"
            f"║         👤  PROFILE          ║\n"
            f"╚══════════════════════════════╝\n\n"
            f"📛 {u.full_name or 'N/A'}\n"
            f"🔖 @{u.username}\n"
            f"🆔 {ig_client.user_id}\n\n"
            f"━━━━━ 📊 Stats ━━━━━\n"
            f"👥  Followers:   {u.follower_count:,}\n"
            f"➡️  Following:   {u.following_count:,}\n"
            f"📸  Total Posts: {u.media_count:,}\n"
            f"❤️  Avg Likes:   {avg_l:,}\n"
            f"💬  Avg Comments:{avg_c:,}\n"
            f"📈  Eng. Rate:   {er}\n\n"
            f"━━━━━ ℹ️ Info ━━━━━\n"
            f"📝 {(u.biography or 'None')[:100]}\n"
            f"🔗 {u.external_url or 'None'}\n"
            f"🔒 Private: {'Yes' if u.is_private else 'No'}\n"
            f"✅ Verified: {'Yes' if u.is_verified else 'No'}\n"
            f"📅 Last Post: {last}"
        )
        await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Analytics",   callback_data="m_analytics"),
             InlineKeyboardButton("📈 Growth",      callback_data="m_growth")],
            [InlineKeyboardButton("✏️ Edit Bio",    callback_data="m_bio"),
             InlineKeyboardButton("🔄 Refresh",     callback_data="m_profile")],
            [InlineKeyboardButton("🏠 Menu",        callback_data="m_home")]
        ]))
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

# ── POST PHOTO ────────────────────────────────────────────────────────────────
async def cmd_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return ST_IMAGE if False else None
    if not ig_logged_in: return
    await update.message.reply_text("📸 Post Photo\n\n━━━━━━━━━━━━━━\nPhoto upload karo:")
    return ST_IMAGE

async def h_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Photo bhejo."); return ST_IMAGE
    msg = await update.message.reply_text("⠋ Processing photo...")
    try:
        f    = await (update.message.photo[-1] if update.message.photo else update.message.document).get_file()
        path = f"/tmp/ig_{update.effective_user.id}.jpg"
        await f.download_to_drive(path)
        ctx.user_data["img"] = path
        await msg.edit_text(
            "✅ Photo ready!\n\n"
            "━━━━━━━━━━━━━━\n"
            "📝 Caption bhejo\n"
            "(ya /skip type karo bina caption ke):"
        )
        return ST_CAP
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
        return ConversationHandler.END

async def h_caption(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cap = "" if update.message.text.strip().lower()=="/skip" else update.message.text.strip()
    ctx.user_data["cap"] = cap
    await update.message.reply_text(
        f"📸 Ready to Post!\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"📝 Caption: {cap[:150] or '(No caption)'}\n\n"
        f"Choose action:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Post Now!",    callback_data="do_post"),
             InlineKeyboardButton("⏰ Schedule",     callback_data="do_schedule")],
            [InlineKeyboardButton("❌ Cancel",       callback_data="m_home")]
        ])
    )
    return ConversationHandler.END

# ── POST STORY ────────────────────────────────────────────────────────────────
async def cmd_story(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "📱 Post Story\n\n━━━━━━━━━━━━━━\nKaunsi type?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ Image Story", callback_data="story_img"),
             InlineKeyboardButton("✍️ Text Story",  callback_data="story_txt")],
            [InlineKeyboardButton("❌ Cancel",       callback_data="m_home")]
        ])
    )

async def h_story_img(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Image bhejo."); return ST_STORY_IMG
    msg = await update.message.reply_text("📱 Uploading story...")
    try:
        f    = await (update.message.photo[-1] if update.message.photo else update.message.document).get_file()
        path = f"/tmp/story_{update.effective_user.id}.jpg"
        await f.download_to_drive(path)
        await anim_progress(msg, "Uploading Story")
        ig_client.photo_upload_to_story(path)
        analytics_data["stories"].append({"type":"image","at":str(datetime.now())}); save_analytics()
        await msg.edit_text("✅ Story posted!", reply_markup=back_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

async def h_story_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg  = await update.message.reply_text("🎨 Creating story image...")
    colors = [(25,25,112),(100,0,100),(0,60,40),(60,0,80),(120,40,0),(0,0,100)]
    bg     = random.choice(colors)
    img    = Image.new("RGB",(1080,1920), color=bg)
    draw   = ImageDraw.Draw(img)
    for y in range(1920):
        t = y/1920
        r = int(bg[0]*(1-t) + min(bg[0]+120,255)*t)
        g = int(bg[1]*(1-t) + min(bg[1]+100,255)*t)
        b = int(bg[2]*(1-t) + min(bg[2]+150,255)*t)
        draw.line([(0,y),(1080,y)], fill=(r,g,b))
    for _ in range(5):
        cx,cy = random.randint(0,1080), random.randint(0,1920)
        cr    = random.randint(80,200)
        draw.ellipse([(cx-cr,cy-cr),(cx+cr,cy+cr)], fill=(255,255,255))
    try:
        font  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
        fontS = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 44)
    except:
        font = fontS = ImageFont.load_default()
    words = text.split(); lines,ln = [],""
    for w in words:
        t2 = f"{ln} {w}".strip()
        if draw.textlength(t2, font=font) < 960: ln = t2
        else: lines.append(ln); ln = w
    lines.append(ln)
    y_start = (1920 - len(lines)*96)//2
    for l in lines:
        w2 = draw.textlength(l, font=font)
        draw.text(((1080-w2)/2+3, y_start+3), l, fill=(0,0,0), font=font)
        draw.text(((1080-w2)/2,   y_start),   l, fill="white", font=font)
        y_start += 96
    wm  = f"@{ig_username}"
    ww  = draw.textlength(wm, font=fontS)
    draw.text(((1080-ww)/2, 1840), wm, fill=(255,255,255), font=fontS)
    buf = BytesIO(); img.save(buf,"JPEG",quality=95); buf.seek(0)
    await anim_progress(msg,"Uploading Story",6)
    try:
        ig_client.photo_upload_to_story(buf)
        analytics_data["stories"].append({"text":text,"at":str(datetime.now())}); save_analytics()
        await msg.edit_text("✅ Story posted!", reply_markup=back_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Upload error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── REEL ──────────────────────────────────────────────────────────────────────
async def cmd_reel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "🎬 Post Reel\n\n━━━━━━━━━━━━━━\n"
        "Video file bhejo (MP4 format)\n"
        "Duration: 3-90 seconds"
    )
    return ST_REEL

async def h_reel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.video and not update.message.document:
        await update.message.reply_text("❌ Video file bhejo (MP4)."); return ST_REEL
    msg = await update.message.reply_text("⠋ Downloading video...")
    try:
        f    = await (update.message.video or update.message.document).get_file()
        path = f"/tmp/reel_{update.effective_user.id}.mp4"
        await f.download_to_drive(path)
        await anim_progress(msg,"Uploading Reel",8)
        ig_client.clip_upload(path, caption=ctx.user_data.get("cap",""))
        analytics_data["reels"].append({"at":str(datetime.now())}); save_analytics()
        await msg.edit_text("✅ Reel posted!", reply_markup=back_kb())
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}\n\nNote: Video 3-90 sec MP4 hona chahiye", reply_markup=back_kb())
    return ConversationHandler.END

# ── FOLLOW ────────────────────────────────────────────────────────────────────
async def cmd_follow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("➕ Follow User\n\n━━━━━━━━━━━━━━\nUsername bhejo:"); return ST_FOLLOW

async def h_follow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uname = update.message.text.strip().lstrip("@")
    msg   = await update.message.reply_text(f"⠋ Looking up @{uname}...")
    try:
        uid = ig_client.user_id_from_username(uname)
        await anim_progress(msg, f"Following @{uname}", 5)
        ig_client.user_follow(uid)
        analytics_data["follows"].append({"u":uname,"at":str(datetime.now())}); save_analytics()
        await msg.edit_text(
            f"✅ @{uname} ko follow kar liya!\n\n"
            f"Total follows today: {len([f for f in analytics_data['follows'] if str(datetime.now().date()) in f.get('at','')])}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Follow Another", callback_data="m_follow"),
                 InlineKeyboardButton("🏠 Menu",           callback_data="m_home")]
            ])
        )
    except UserNotFound: await msg.edit_text(f"❌ @{uname} nahi mila.", reply_markup=back_kb())
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── UNFOLLOW ──────────────────────────────────────────────────────────────────
async def cmd_unfollow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("➖ Unfollow User\n\n━━━━━━━━━━━━━━\nUsername bhejo:"); return ST_UNFOLLOW

async def h_unfollow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uname = update.message.text.strip().lstrip("@")
    msg   = await update.message.reply_text(f"⠋ @{uname} unfollow ho raha hai...")
    try:
        uid = ig_client.user_id_from_username(uname)
        await anim_progress(msg, f"Unfollowing @{uname}", 5)
        ig_client.user_unfollow(uid)
        analytics_data["unfollows"].append({"u":uname,"at":str(datetime.now())}); save_analytics()
        await msg.edit_text(f"✅ @{uname} ko unfollow kar diya!", reply_markup=back_kb())
    except UserNotFound: await msg.edit_text(f"❌ @{uname} nahi mila.", reply_markup=back_kb())
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── BULK FOLLOW ───────────────────────────────────────────────────────────────
async def cmd_bulk_follow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "⚡ Bulk Follow\n\n━━━━━━━━━━━━━━\n"
        "Multiple usernames bhejo\n"
        "Ek line mein ek username:\n\n"
        "Example:\nuser1\nuser2\nuser3"
    )
    return ST_BULK_FOLLOW

async def h_bulk_follow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usernames = [u.strip().lstrip("@") for u in update.message.text.strip().split("\n") if u.strip()]
    msg       = await update.message.reply_text(f"⚡ {len(usernames)} users ko follow karna hai...")
    results   = {"success":[],"failed":[]}
    for i, uname in enumerate(usernames[:20]):
        try:
            await msg.edit_text(f"⚡ Following {i+1}/{len(usernames)}: @{uname}...")
            uid = ig_client.user_id_from_username(uname)
            ig_client.user_follow(uid)
            results["success"].append(uname)
            analytics_data["follows"].append({"u":uname,"at":str(datetime.now())})
            await asyncio.sleep(random.uniform(2,4))
        except: results["failed"].append(uname)
    save_analytics()
    txt = (f"⚡ Bulk Follow Complete!\n\n"
           f"✅ Success: {len(results['success'])}\n"
           f"❌ Failed:  {len(results['failed'])}\n\n")
    if results["success"]: txt += "Followed:\n" + "\n".join(f"• @{u}" for u in results["success"][:10])
    if results["failed"]:  txt += "\n\nFailed:\n"  + "\n".join(f"• @{u}" for u in results["failed"][:5])
    await msg.edit_text(txt, reply_markup=back_kb())
    return ConversationHandler.END

# ── BULK UNFOLLOW ─────────────────────────────────────────────────────────────
async def cmd_bulk_unfollow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "🗑️ Bulk Unfollow\n\n━━━━━━━━━━━━━━\n"
        "Multiple usernames bhejo (ek per line):\n\n"
        "Ya type karo 'non-followers' to unfollow\n"
        "all who don't follow you back"
    )
    return ST_BULK_UNFOLLOW

async def h_bulk_unfollow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg  = await update.message.reply_text("🗑️ Processing...")
    if text.lower() == "non-followers":
        try:
            await msg.edit_text("⠋ Fetching followers & following...")
            followers  = set(ig_client.user_followers(ig_client.user_id, amount=500).keys())
            following  = set(ig_client.user_following(ig_client.user_id, amount=500).keys())
            non_follow = following - followers
            await msg.edit_text(f"Found {len(non_follow)} non-followers.\n\nUnfollowing...")
            count = 0
            for uid in list(non_follow)[:30]:
                try:
                    ig_client.user_unfollow(uid); count+=1
                    await asyncio.sleep(random.uniform(2,4))
                except: pass
            analytics_data["unfollows"].append({"bulk":"non-followers","count":count,"at":str(datetime.now())}); save_analytics()
            await msg.edit_text(f"✅ {count} non-followers ko unfollow kar diya!", reply_markup=back_kb())
        except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    else:
        usernames = [u.strip().lstrip("@") for u in text.split("\n") if u.strip()]
        done = 0
        for i,uname in enumerate(usernames[:20]):
            try:
                await msg.edit_text(f"🗑️ Unfollowing {i+1}/{len(usernames)}: @{uname}...")
                uid=ig_client.user_id_from_username(uname); ig_client.user_unfollow(uid)
                analytics_data["unfollows"].append({"u":uname,"at":str(datetime.now())}); done+=1
                await asyncio.sleep(random.uniform(2,4))
            except: pass
        save_analytics()
        await msg.edit_text(f"✅ {done}/{len(usernames)} users unfollow ho gaye!", reply_markup=back_kb())
    return ConversationHandler.END

# ── SEARCH ────────────────────────────────────────────────────────────────────
async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("🔍 Search User\n\n━━━━━━━━━━━━━━\nUsername bhejo:"); return ST_SEARCH

async def h_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uname = update.message.text.strip().lstrip("@")
    msg   = await update.message.reply_text(f"🔍 Searching @{uname}...")
    try:
        uid  = ig_client.user_id_from_username(uname)
        info = ig_client.user_info(uid)
        try:
            fr  = ig_client.user_friendship(uid)
            ifl = "✅ Yes" if fr.following  else "❌ No"
            tfl = "✅ Yes" if fr.followed_by else "❌ No"
        except: ifl = tfl = "N/A"
        txt = (
            f"🔍 @{uname}\n\n"
            f"━━━━━ 👤 Info ━━━━━\n"
            f"📛 {info.full_name or 'N/A'}\n"
            f"🆔 {uid}\n\n"
            f"━━━━━ 📊 Stats ━━━━━\n"
            f"👥 Followers: {info.follower_count:,}\n"
            f"➡️ Following: {info.following_count:,}\n"
            f"📸 Posts:     {info.media_count:,}\n\n"
            f"━━━━━ 🤝 Relationship ━━━━━\n"
            f"You → Them:   {ifl}\n"
            f"Them → You:   {tfl}\n\n"
            f"🔒 Private: {'Yes' if info.is_private else 'No'}\n"
            f"✅ Verified: {'Yes' if info.is_verified else 'No'}\n\n"
            f"📝 {(info.biography or 'None')[:100]}"
        )
        is_fl = "Yes" in ifl
        await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➖ Unfollow" if is_fl else "➕ Follow",
                callback_data=f"q_unfl_{uname}" if is_fl else f"q_fl_{uname}"),
             InlineKeyboardButton("❤️ Like Posts", callback_data=f"q_like_{uname}")],
            [InlineKeyboardButton("📸 Their Posts", callback_data=f"u_posts_{uname}"),
             InlineKeyboardButton("💬 Comment",     callback_data=f"q_cmt_{uname}")],
            [InlineKeyboardButton("📩 Send DM",     callback_data=f"q_dm_{uname}"),
             InlineKeyboardButton("🏠 Menu",        callback_data="m_home")]
        ]))
    except UserNotFound: await msg.edit_text(f"❌ @{uname} nahi mila.", reply_markup=back_kb())
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── LIKE ──────────────────────────────────────────────────────────────────────
async def cmd_like(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("❤️ Auto Like\n\n━━━━━━━━━━━━━━\nKis user ki posts like karni hain?\nUsername bhejo:"); return ST_LIKE

async def h_like(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uname = update.message.text.strip().lstrip("@")
    msg   = await update.message.reply_text(f"❤️ @{uname} ki posts like ho rahi hain...")
    try:
        uid   = ig_client.user_id_from_username(uname)
        media = ig_client.user_medias(uid, 6)
        liked = 0
        for i,m in enumerate(media[:6]):
            try:
                await msg.edit_text(f"❤️ Liking {i+1}/{min(len(media),6)} posts...")
                ig_client.media_like(m.id); liked+=1
                await asyncio.sleep(random.uniform(1.5,3.5))
            except: pass
        analytics_data["likes"].append({"u":uname,"count":liked,"at":str(datetime.now())}); save_analytics()
        await msg.edit_text(
            f"✅ {liked} posts liked!\n\nUser: @{uname}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❤️ Like More",callback_data="m_like"),
                 InlineKeyboardButton("🏠 Menu",     callback_data="m_home")]
            ])
        )
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── COMMENT ───────────────────────────────────────────────────────────────────
async def cmd_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "💬 Comment\n\n━━━━━━━━━━━━━━\n"
        "Format: username | comment\n\n"
        "Example:\njohn_doe | Great photo! 🔥"
    )
    return ST_COMMENT

async def h_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split("|",1)
    if len(parts)!=2:
        await update.message.reply_text("❌ Format: username | comment\nExample: john_doe | Nice! 🔥")
        return ST_COMMENT
    uname, comment = parts[0].strip().lstrip("@"), parts[1].strip()
    msg = await update.message.reply_text(f"💬 Commenting on @{uname}'s post...")
    try:
        uid   = ig_client.user_id_from_username(uname)
        media = ig_client.user_medias(uid,1)
        if not media: await msg.edit_text("❌ Koi post nahi mili."); return ConversationHandler.END
        ig_client.media_comment(media[0].id, comment)
        analytics_data["comments"].append({"u":uname,"c":comment,"at":str(datetime.now())}); save_analytics()
        await msg.edit_text(f"✅ Comment posted!\n\n@{uname}: \"{comment}\"", reply_markup=back_kb())
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── SEND DM ───────────────────────────────────────────────────────────────────
async def cmd_dm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "📩 Send DM\n\n━━━━━━━━━━━━━━\n"
        "Format: username | message\n\n"
        "Example:\njohn_doe | Hey! Check my latest post 🔥"
    )
    return ST_DM_USER

async def h_dm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split("|",1)
    if len(parts)!=2:
        await update.message.reply_text("❌ Format: username | message"); return ST_DM_USER
    uname, message = parts[0].strip().lstrip("@"), parts[1].strip()
    msg = await update.message.reply_text(f"📩 DM bhej raha hoon @{uname} ko...")
    try:
        uid = ig_client.user_id_from_username(uname)
        ig_client.direct_send(message, user_ids=[uid])
        analytics_data["dms"].append({"u":uname,"at":str(datetime.now())}); save_analytics()
        await msg.edit_text(f"✅ DM sent to @{uname}!\n\nMessage: \"{message[:100]}\"", reply_markup=back_kb())
    except Exception as e: await msg.edit_text(f"❌ DM error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── EDIT BIO ──────────────────────────────────────────────────────────────────
async def cmd_bio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    try:
        u = ig_client.account_info()
        await update.message.reply_text(
            f"✏️ Edit Bio\n\n━━━━━━━━━━━━━━\n"
            f"Current Bio:\n_{(u.biography or 'Empty')}_\n\n"
            f"Naya bio bhejo:",
            parse_mode="Markdown"
        )
    except:
        await update.message.reply_text("✏️ Edit Bio\n\nNaya bio bhejo:")
    return ST_BIO_TEXT

async def h_bio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_bio = update.message.text.strip()
    msg = await update.message.reply_text("✏️ Bio update ho raha hai...")
    try:
        ig_client.account_edit(biography=new_bio)
        await msg.edit_text(f"✅ Bio updated!\n\nNew Bio:\n\"{new_bio}\"", reply_markup=back_kb())
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())
    return ConversationHandler.END

# ── HASHTAG TOOL ──────────────────────────────────────────────────────────────
async def cmd_hashtags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("🏷️ Hashtag Generator\n\n━━━━━━━━━━━━━━\nTopic bhejo (e.g. travel, fitness, food):"); return ST_HASHTAG_TOPIC

async def h_hashtags(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    topic = update.message.text.strip()
    msg   = await update.message.reply_text(f"🏷️ Generating hashtags for '{topic}'...")
    base = topic.lower().replace(" ","")
    tags = [
        f"#{base}", f"#{base}photography", f"#{base}lover", f"#{base}life",
        f"#{base}gram", f"#{base}daily", f"#{base}india", f"#{base}world",
        f"#{base}community", f"#{base}official",
        "#instagood","#photooftheday","#instadaily","#picoftheday","#follow",
        "#like4like","#followforfollow","#likeforlikes","#instagram","#viral",
        "#trending","#explore","#explorepage","#reels","#instareels",
        "#fyp","#foryou","#india","#indianinstagram","#desi"
    ]
    random.shuffle(tags)
    txt = (
        f"🏷️ Hashtags for '{topic}':\n\n"
        f"━━━━━ Copy Karo ━━━━━\n\n"
        + " ".join(tags[:30]) +
        f"\n\n━━━━━━━━━━━━━━\n"
        f"Total: {len(tags[:30])} hashtags\n"
        f"💡 Tip: Story aur caption dono mein use karo!"
    )
    await msg.edit_text(txt, reply_markup=back_kb())
    return ConversationHandler.END

# ── GHOST CHECK ───────────────────────────────────────────────────────────────
async def show_ghost_check(query):
    if not ig_logged_in: await query.edit_message_text("❌ Login karo pehle"); return
    msg = await query.edit_message_text("🕵️ Ghost followers check ho raha hai...")
    try:
        await msg.edit_text("⠋ Fetching your followers...")
        followers = ig_client.user_followers(ig_client.user_id, amount=100)
        await msg.edit_text("⠙ Fetching following list...")
        following = ig_client.user_following(ig_client.user_id, amount=100)
        fl_set   = set(followers.keys())
        fg_set   = set(following.keys())
        not_back = fg_set - fl_set
        fans     = fl_set - fg_set
        mutual   = fl_set & fg_set
        txt = (
            f"🕵️ Ghost Check Report\n\n"
            f"━━━━━ Results ━━━━━\n"
            f"👥 Sample size: {len(fl_set)+len(fg_set)} users\n\n"
            f"❌ Not following back: {len(not_back)}\n"
            f"💚 Your fans (follow you, u don't): {len(fans)}\n"
            f"🤝 Mutual follows: {len(mutual)}\n\n"
            f"━━━━━ Top Non-Followers ━━━━━\n"
        )
        for i,uid in enumerate(list(not_back)[:5],1):
            u = following.get(uid)
            if u: txt += f"{i}. @{u.username}\n"
        txt += f"\n💡 Use Bulk Unfollow to remove non-followers!"
        await msg.edit_text(txt, reply_markup=back_refresh_kb("m_ghost"))
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

# ── GROWTH STATS ──────────────────────────────────────────────────────────────
async def show_growth(query):
    if not ig_logged_in: await query.edit_message_text("❌ Login karo"); return
    msg = await query.edit_message_text("📈 Growth stats load ho rahi hain...")
    load_analytics()
    try:
        u     = ig_client.account_info()
        today = str(datetime.now().date())
        fl_today = sum(1 for f in analytics_data.get("follows",[])   if today in f.get("at",""))
        un_today = sum(1 for f in analytics_data.get("unfollows",[]) if today in f.get("at",""))
        lk_today = sum(1 for f in analytics_data.get("likes",[])     if today in f.get("at",""))
        pt_today = sum(1 for f in analytics_data.get("posts",[])     if today in f.get("at",""))
        txt = (
            f"📈 Growth Stats\n\n"
            f"━━━━━ 📊 Account ━━━━━\n"
            f"👥 Followers:  {u.follower_count:,}\n"
            f"➡️ Following:  {u.following_count:,}\n"
            f"📸 Posts:      {u.media_count:,}\n"
            f"📊 F/F Ratio:  {u.follower_count/max(u.following_count,1):.2f}\n\n"
            f"━━━━━ 📅 Today's Activity ━━━━━\n"
            f"➕ Follows:   {fl_today}\n"
            f"➖ Unfollows: {un_today}\n"
            f"❤️ Likes:     {lk_today}\n"
            f"📤 Posts:     {pt_today}\n\n"
            f"━━━━━ 📆 All Time ━━━━━\n"
            f"Total Follows:   {len(analytics_data.get('follows',[]))}\n"
            f"Total Unfollows: {len(analytics_data.get('unfollows',[]))}\n"
            f"Total Likes:     {len(analytics_data.get('likes',[]))}\n"
            f"Total Posts:     {len(analytics_data.get('posts',[]))}\n"
            f"Total Stories:   {len(analytics_data.get('stories',[]))}\n"
            f"Total DMs:       {len(analytics_data.get('dms',[]))}"
        )
        await msg.edit_text(txt, reply_markup=back_refresh_kb("m_growth"))
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

# ── NOTIFICATIONS ─────────────────────────────────────────────────────────────
async def show_notifs(query):
    if not ig_logged_in: await query.edit_message_text("❌ Login karo"); return
    msg = await query.edit_message_text("🔔 Notifications load ho rahi hain...")
    try:
        news = ig_client.news_inbox_v1()
        txt  = "🔔 Recent Notifications\n\n━━━━━━━━━━━━━━\n"
        if hasattr(news,'new_stories') and news.new_stories:
            for n in news.new_stories[:10]:
                txt += f"• {str(n)[:80]}\n"
        else:
            txt += "📭 Koi new notifications nahi"
        await msg.edit_text(txt, reply_markup=back_refresh_kb("m_notifs"))
    except Exception as e:
        await msg.edit_text(f"🔔 Notifications\n\n❌ Error: {e}\n\nInstagram API limitation.", reply_markup=back_kb())

# ── ANALYTICS ─────────────────────────────────────────────────────────────────
async def show_analytics(query):
    load_analytics()
    pending = sum(1 for p in scheduled_posts if p.get("status")=="scheduled")
    txt = (
        f"╔══════════════════════════════╗\n"
        f"║       📊  ANALYTICS          ║\n"
        f"╚══════════════════════════════╝\n\n"
        f"━━━━━ 📤 Content ━━━━━\n"
        f"📸  Photos:    {len(analytics_data.get('posts',[]))}\n"
        f"📱  Stories:   {len(analytics_data.get('stories',[]))}\n"
        f"🎬  Reels:     {len(analytics_data.get('reels',[]))}\n"
        f"⏳  Scheduled: {pending}\n\n"
        f"━━━━━ 🤝 Social ━━━━━\n"
        f"➕  Follows:   {len(analytics_data.get('follows',[]))}\n"
        f"➖  Unfollows: {len(analytics_data.get('unfollows',[]))}\n"
        f"❤️  Likes:     {len(analytics_data.get('likes',[]))}\n"
        f"💬  Comments:  {len(analytics_data.get('comments',[]))}\n"
        f"📩  DMs Sent:  {len(analytics_data.get('dms',[]))}"
    )
    if ig_logged_in and ig_client:
        try:
            u = ig_client.account_info()
            txt += (f"\n\n━━━━━ 📈 Live ━━━━━\n"
                    f"👥 Followers: {u.follower_count:,}\n"
                    f"📸 Posts:     {u.media_count:,}")
        except: pass
    await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Growth",   callback_data="m_growth"),
         InlineKeyboardButton("🔄 Refresh",  callback_data="m_analytics")],
        [InlineKeyboardButton("🏠 Menu",     callback_data="m_home")]
    ]))

# ── FOLLOWERS / FOLLOWING / MY POSTS ─────────────────────────────────────────
async def show_followers(query):
    msg = await query.edit_message_text("⠋ Loading followers...")
    try:
        fl  = ig_client.user_followers(ig_client.user_id, amount=20)
        txt = "👥 Your Followers (Top 20):\n\n"
        for i,(uid,u) in enumerate(list(fl.items())[:20],1):
            txt += f"{i:2}. @{u.username} {'✅' if u.is_verified else ''}\n"
        await msg.edit_text(txt, reply_markup=back_refresh_kb("m_followers"))
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

async def show_following(query):
    msg = await query.edit_message_text("⠋ Loading following...")
    try:
        fl  = ig_client.user_following(ig_client.user_id, amount=20)
        txt = "➡️ You Follow (Top 20):\n\n"
        for i,(uid,u) in enumerate(list(fl.items())[:20],1):
            txt += f"{i:2}. @{u.username}\n"
        await msg.edit_text(txt, reply_markup=back_refresh_kb("m_following"))
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

async def show_my_posts(query):
    msg = await query.edit_message_text("⠋ Loading your posts...")
    try:
        media = ig_client.user_medias(ig_client.user_id, 10)
        if not media: await msg.edit_text("📭 Koi post nahi.", reply_markup=back_kb()); return
        txt = "📸 My Last 10 Posts:\n\n"
        for i,m in enumerate(media,1):
            d   = m.taken_at.strftime('%d %b %Y') if m.taken_at else 'N/A'
            cap = (m.caption_text or "No caption")[:35]
            txt += f"{i}. {d}\n   ❤️{m.like_count or 0:,}  💬{m.comment_count or 0:,}\n   _{cap}_\n\n"
        await msg.edit_text(txt, parse_mode="Markdown", reply_markup=back_refresh_kb("m_myposts"))
    except Exception as e: await msg.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

# ── SCHEDULE ──────────────────────────────────────────────────────────────────
async def cmd_sched(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in: await update.message.reply_text("❌ Pehle /login karo\nPhir /post se image set karo"); return
    if not ctx.user_data.get("img"): await update.message.reply_text("❌ Pehle /post se image aur caption set karo"); return
    await update.message.reply_text(
        "⏰ Schedule Post\n\n━━━━━━━━━━━━━━\n"
        "Time batao:\nFormat: DD/MM/YYYY HH:MM\n\n"
        "Example: 25/07/2025 20:30"
    )
    return ST_SCHED_TIME

async def h_sched(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        st  = datetime.strptime(update.message.text.strip(), "%d/%m/%Y %H:%M")
        img = ctx.user_data.get("img",""); cap = ctx.user_data.get("cap","")
        pid = len(scheduled_posts)+1
        scheduled_posts.append({"id":pid,"time":st,"img":img,"cap":cap,"status":"scheduled"})
        delay = (st-datetime.now()).total_seconds()
        if delay>0: threading.Timer(delay, lambda: asyncio.run(_do_sched(pid,img,cap))).start()
        await update.message.reply_text(
            f"✅ Post Scheduled!\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"📅 {st.strftime('%d %b %Y, %I:%M %p')}\n"
            f"🆔 Post ID: #{pid}\n"
            f"Status: ⏳ Pending",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 View Scheduled",callback_data="m_scheduled"),
                 InlineKeyboardButton("🏠 Menu",          callback_data="m_home")]
            ])
        )
    except ValueError:
        await update.message.reply_text("❌ Format galat.\nUse: DD/MM/YYYY HH:MM")
    return ConversationHandler.END

async def _do_sched(pid,img,cap):
    try:
        ig_client.photo_upload(img,caption=cap)
        analytics_data["posts"].append({"cap":cap[:80],"at":str(datetime.now())}); save_analytics()
        for p in scheduled_posts:
            if p["id"]==pid: p["status"]="posted"
    except:
        for p in scheduled_posts:
            if p["id"]==pid: p["status"]="failed"

async def show_scheduled(query):
    if not scheduled_posts:
        await query.edit_message_text("📭 Koi scheduled post nahi.", reply_markup=back_kb()); return
    txt = "📅 Scheduled Posts:\n\n━━━━━━━━━━━━━━\n"
    for p in scheduled_posts[-10:]:
        e = {"scheduled":"⏳","posted":"✅","failed":"❌"}.get(p["status"],"❓")
        t = p["time"].strftime('%d %b %Y %I:%M %p') if isinstance(p["time"],datetime) else str(p["time"])
        txt += f"{e} #{p['id']}\n   📅 {t}\n   Status: {p['status']}\n\n"
    await query.edit_message_text(txt, reply_markup=back_refresh_kb("m_scheduled"))

# ── SETTINGS ──────────────────────────────────────────────────────────────────
async def show_settings(query):
    txt = (
        f"⚙️ Settings\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"🤖 Bot: {VERSION}\n"
        f"👤 Account: {'@'+ig_username if ig_logged_in else 'Not logged in'}\n"
        f"💾 Session: {'✅ Saved' if Path(SESSION_FILE).exists() else '❌ Not saved'}\n"
        f"📊 Analytics: {'✅ Active' if Path(ANALYTICS_FILE).exists() else '❌ No data'}\n"
        f"⏳ Scheduled Posts: {len(scheduled_posts)}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔐 Session auto-login: ✅ ON\n"
        f"📱 Notifications: ✅ ON\n"
        f"⚡ Bulk actions limit: 20/run"
    )
    await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Clear Session",   callback_data="clear_session"),
         InlineKeyboardButton("📊 Reset Analytics", callback_data="reset_analytics")],
        [InlineKeyboardButton("🏠 Menu",            callback_data="m_home")]
    ]))

# ── /cancel ───────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_kb(ig_logged_in))
    return ConversationHandler.END

# ── CALLBACK ROUTER ───────────────────────────────────────────────────────────
async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); d = q.data

    if d == "noop": return

    elif d == "m_home":
        name = q.from_user.first_name or "User"
        now  = datetime.now().strftime("%I:%M %p")
        txt  = (
            f"╔══════════════════════════════╗\n"
            f"║   📸  INSTAGRAM PRO BOT  📸  ║\n"
            f"╚══════════════════════════════╝\n\n"
            f"👋 {name}  •  🕐 {now}\n"
            f"{'✅ @'+ig_username if ig_logged_in else '❌ Not logged in'}\n\n"
            f"Select an option 👇"
        )
        await q.edit_message_text(txt, reply_markup=main_kb(ig_logged_in))

    elif d == "m_login":   await q.edit_message_text("🔐 Type /login to start:")
    elif d == "m_about":
        await q.edit_message_text(
            f"ℹ️ About\n\n"
            f"📸 Instagram Pro Bot\n"
            f"🏷️ {VERSION}\n\n"
            f"Features:\n"
            f"• Persistent login (auto session)\n"
            f"• 2FA & Email OTP support\n"
            f"• Post photos, stories, reels\n"
            f"• Follow / Unfollow / Bulk actions\n"
            f"• Auto like & comment\n"
            f"• Send DMs\n"
            f"• Ghost follower check\n"
            f"• Growth analytics\n"
            f"• Post scheduling\n"
            f"• Live status & test panel\n"
            f"• Edit bio & hashtag tools\n"
            f"• Persistent bottom keyboard",
            reply_markup=back_kb()
        )

    elif d == "m_live":      await show_live(q, edit=True)
    elif d == "m_livetest":  await show_live_test(q)
    elif d == "m_profile":   await show_profile(q)
    elif d == "m_analytics": await show_analytics(q)
    elif d == "m_followers": await show_followers(q)
    elif d == "m_following": await show_following(q)
    elif d == "m_myposts":   await show_my_posts(q)
    elif d == "m_scheduled": await show_scheduled(q)
    elif d == "m_ghost":     await show_ghost_check(q)
    elif d == "m_growth":    await show_growth(q)
    elif d == "m_notifs":    await show_notifs(q)
    elif d == "m_settings":  await show_settings(q)

    elif d == "m_help":
        await q.edit_message_text(
            "📖 All Commands\n\n"
            "━━━━━ Account ━━━━━\n"
            "/start    — Main menu + refresh keyboard\n"
            "/login    — Instagram login\n"
            "/logout   — Logout\n"
            "/live     — Live account status\n\n"
            "━━━━━ Content ━━━━━\n"
            "/post     — Post photo\n"
            "/story    — Post story\n"
            "/reel     — Post reel\n"
            "/schedule — Schedule post\n\n"
            "━━━━━ Social ━━━━━\n"
            "/follow   — Follow user\n"
            "/unfollow — Unfollow user\n"
            "/like     — Auto like posts\n"
            "/comment  — Comment on post\n"
            "/dm       — Send DM\n"
            "/search   — Search & inspect user\n\n"
            "━━━━━ Premium ━━━━━\n"
            "/bulkfollow   — Follow multiple\n"
            "/bulkunfollow — Unfollow multiple\n"
            "/hashtags     — Generate hashtags\n"
            "/bio          — Edit your bio\n\n"
            "━━━━━ Info ━━━━━\n"
            "/cancel    — Cancel operation",
            reply_markup=back_kb()
        )

    elif d == "m_logout_confirm":
        await q.edit_message_text(
            f"🚪 Logout Confirm?\n\n@{ig_username} se disconnect hoge.\nSession delete ho jayega.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Haan, Logout", callback_data="do_logout"),
                 InlineKeyboardButton("❌ Nahi",         callback_data="m_home")]
            ])
        )

    elif d == "do_logout":
        await q.edit_message_text("🔓 Logging out...")
        await anim_logout(q.message); do_logout()
        await q.message.edit_text(
            "👋 Logged Out!\n\nSession cleared.\nDobara login karne ke liye /login karo.",
            reply_markup=main_kb(False)
        )

    elif d == "clear_session":
        do_logout()
        await q.edit_message_text("✅ Session cleared! Dobara /login karo.", reply_markup=back_kb())

    elif d == "reset_analytics":
        global analytics_data
        analytics_data = {"posts":[],"stories":[],"reels":[],"follows":[],"unfollows":[],"likes":[],"comments":[],"dms":[],"bulk_follows":[]}
        save_analytics()
        await q.edit_message_text("✅ Analytics reset kar diya!", reply_markup=back_kb())

    elif d in ["m_post","m_story","m_reel","m_follow","m_unfollow","m_search",
               "m_like","m_comment","m_dm","m_bulk_follow","m_bulk_unfollow",
               "m_hashtags","m_bio","m_sched"]:
        cmds = {
            "m_post":"/post","m_story":"/story","m_reel":"/reel",
            "m_follow":"/follow","m_unfollow":"/unfollow","m_search":"/search",
            "m_like":"/like","m_comment":"/comment","m_dm":"/dm",
            "m_bulk_follow":"/bulkfollow","m_bulk_unfollow":"/bulkunfollow",
            "m_hashtags":"/hashtags","m_bio":"/bio","m_sched":"/schedule"
        }
        await q.edit_message_text(f"👉 {cmds[d]} type karo ya send karo:", reply_markup=back_kb())

    elif d == "do_post":
        img = ctx.user_data.get("img",""); cap = ctx.user_data.get("cap","")
        if not img: await q.edit_message_text("❌ Image nahi. /post se try karo."); return
        await q.edit_message_text("⠋ Posting...")
        await anim_progress(q.message, "Uploading to Instagram")
        try:
            ig_client.photo_upload(img, caption=cap)
            analytics_data["posts"].append({"cap":cap[:80],"at":str(datetime.now())}); save_analytics()
            await anim_ok(q.message,"Post uploaded successfully!")
        except Exception as e: await q.message.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

    elif d == "do_schedule":
        await q.edit_message_text("⏰ /schedule type karo aur time batao:\nFormat: DD/MM/YYYY HH:MM", reply_markup=back_kb())

    elif d == "story_img": await q.edit_message_text("🖼️ Image bhejo story ke liye:"); ctx.user_data["st"]="img"
    elif d == "story_txt": await q.edit_message_text("✍️ Text bhejo story ke liye:");  ctx.user_data["st"]="txt"

    elif d.startswith("q_fl_"):
        uname = d[5:]
        await q.edit_message_text(f"➕ Following @{uname}...")
        try:
            uid=ig_client.user_id_from_username(uname); ig_client.user_follow(uid)
            analytics_data["follows"].append({"u":uname,"at":str(datetime.now())}); save_analytics()
            await q.message.edit_text(f"✅ @{uname} follow ho gaya!", reply_markup=back_kb())
        except Exception as e: await q.message.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

    elif d.startswith("q_unfl_"):
        uname = d[7:]
        await q.edit_message_text(f"➖ Unfollowing @{uname}...")
        try:
            uid=ig_client.user_id_from_username(uname); ig_client.user_unfollow(uid)
            analytics_data["unfollows"].append({"u":uname,"at":str(datetime.now())}); save_analytics()
            await q.message.edit_text(f"✅ @{uname} unfollow ho gaya!", reply_markup=back_kb())
        except Exception as e: await q.message.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

    elif d.startswith("q_like_"):
        uname = d[7:]
        await q.edit_message_text(f"❤️ Liking @{uname}'s posts...")
        try:
            uid=ig_client.user_id_from_username(uname); media=ig_client.user_medias(uid,5); liked=0
            for m in media:
                try: ig_client.media_like(m.id); liked+=1; await asyncio.sleep(2)
                except: pass
            await q.message.edit_text(f"✅ {liked} posts liked!", reply_markup=back_kb())
        except Exception as e: await q.message.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

    elif d.startswith("q_cmt_"):
        uname = d[6:]
        await q.edit_message_text(f"💬 Comment karna hai @{uname} ki post pe.\n\nFormat:\n{uname} | comment text\n\nType /comment to proceed.", reply_markup=back_kb())

    elif d.startswith("q_dm_"):
        uname = d[5:]
        await q.edit_message_text(f"📩 DM bhejni hai @{uname} ko.\n\nFormat:\n{uname} | message text\n\nType /dm to proceed.", reply_markup=back_kb())

    elif d.startswith("u_posts_"):
        uname = d[8:]
        await q.edit_message_text(f"⠋ @{uname} ki posts load ho rahi hain...")
        try:
            uid=ig_client.user_id_from_username(uname); media=ig_client.user_medias(uid,6)
            txt=f"📸 @{uname} ke Posts:\n\n"
            for i,m in enumerate(media,1):
                d2=m.taken_at.strftime('%d %b %Y') if m.taken_at else 'N/A'
                txt+=f"{i}. {d2} — ❤️{m.like_count or 0:,} 💬{m.comment_count or 0:,}\n"
            await q.message.edit_text(txt, reply_markup=back_kb())
        except Exception as e: await q.message.edit_text(f"❌ Error: {e}", reply_markup=back_kb())

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    load_analytics()
    if load_session(): print(f"✅ Session restored: @{ig_username}")
    else: print("ℹ️ No session — /login required.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login",cmd_login)],
        states={
            ST_USER:[MessageHandler(filters.TEXT&~filters.COMMAND,h_username)],
            ST_PASS:[MessageHandler(filters.TEXT&~filters.COMMAND,h_password)],
            ST_2FA: [MessageHandler(filters.TEXT&~filters.COMMAND,h_2fa)],
            ST_OTP: [MessageHandler(filters.TEXT&~filters.COMMAND,h_otp)]
        },
        fallbacks=[CommandHandler("cancel",cmd_cancel)]
    )
    post_conv = ConversationHandler(
        entry_points=[CommandHandler("post",cmd_post)],
        states={
            ST_IMAGE:[MessageHandler(filters.PHOTO|filters.Document.IMAGE,h_image)],
            ST_CAP:  [MessageHandler(filters.TEXT&~filters.COMMAND,h_caption)]
        },
        fallbacks=[CommandHandler("cancel",cmd_cancel)]
    )
    story_conv = ConversationHandler(
        entry_points=[CommandHandler("story",cmd_story)],
        states={
            ST_STORY_TEXT:[MessageHandler(filters.TEXT&~filters.COMMAND,h_story_text)],
            ST_STORY_IMG: [MessageHandler(filters.PHOTO|filters.Document.IMAGE,h_story_img)]
        },
        fallbacks=[CommandHandler("cancel",cmd_cancel)]
    )
    reel_conv   = ConversationHandler(entry_points=[CommandHandler("reel",cmd_reel)],         states={ST_REEL:[MessageHandler(filters.VIDEO|filters.Document.VIDEO,h_reel)]},            fallbacks=[CommandHandler("cancel",cmd_cancel)])
    follow_conv = ConversationHandler(entry_points=[CommandHandler("follow",cmd_follow)],      states={ST_FOLLOW:[MessageHandler(filters.TEXT&~filters.COMMAND,h_follow)]},              fallbacks=[CommandHandler("cancel",cmd_cancel)])
    unfl_conv   = ConversationHandler(entry_points=[CommandHandler("unfollow",cmd_unfollow)],  states={ST_UNFOLLOW:[MessageHandler(filters.TEXT&~filters.COMMAND,h_unfollow)]},          fallbacks=[CommandHandler("cancel",cmd_cancel)])
    search_conv = ConversationHandler(entry_points=[CommandHandler("search",cmd_search)],      states={ST_SEARCH:[MessageHandler(filters.TEXT&~filters.COMMAND,h_search)]},              fallbacks=[CommandHandler("cancel",cmd_cancel)])
    like_conv   = ConversationHandler(entry_points=[CommandHandler("like",cmd_like)],          states={ST_LIKE:[MessageHandler(filters.TEXT&~filters.COMMAND,h_like)]},                  fallbacks=[CommandHandler("cancel",cmd_cancel)])
    cmt_conv    = ConversationHandler(entry_points=[CommandHandler("comment",cmd_comment)],    states={ST_COMMENT:[MessageHandler(filters.TEXT&~filters.COMMAND,h_comment)]},            fallbacks=[CommandHandler("cancel",cmd_cancel)])
    dm_conv     = ConversationHandler(entry_points=[CommandHandler("dm",cmd_dm)],              states={ST_DM_USER:[MessageHandler(filters.TEXT&~filters.COMMAND,h_dm)]},                 fallbacks=[CommandHandler("cancel",cmd_cancel)])
    bkfl_conv   = ConversationHandler(entry_points=[CommandHandler("bulkfollow",cmd_bulk_follow)],   states={ST_BULK_FOLLOW:[MessageHandler(filters.TEXT&~filters.COMMAND,h_bulk_follow)]},   fallbacks=[CommandHandler("cancel",cmd_cancel)])
    bkun_conv   = ConversationHandler(entry_points=[CommandHandler("bulkunfollow",cmd_bulk_unfollow)],states={ST_BULK_UNFOLLOW:[MessageHandler(filters.TEXT&~filters.COMMAND,h_bulk_unfollow)]},fallbacks=[CommandHandler("cancel",cmd_cancel)])
    bio_conv    = ConversationHandler(entry_points=[CommandHandler("bio",cmd_bio)],            states={ST_BIO_TEXT:[MessageHandler(filters.TEXT&~filters.COMMAND,h_bio)]},               fallbacks=[CommandHandler("cancel",cmd_cancel)])
    hash_conv   = ConversationHandler(entry_points=[CommandHandler("hashtags",cmd_hashtags)],  states={ST_HASHTAG_TOPIC:[MessageHandler(filters.TEXT&~filters.COMMAND,h_hashtags)]},     fallbacks=[CommandHandler("cancel",cmd_cancel)])
    sched_conv  = ConversationHandler(entry_points=[CommandHandler("schedule",cmd_sched)],     states={ST_SCHED_TIME:[MessageHandler(filters.TEXT&~filters.COMMAND,h_sched)]},           fallbacks=[CommandHandler("cancel",cmd_cancel)])

    for c in [login_conv,post_conv,story_conv,reel_conv,follow_conv,unfl_conv,
              search_conv,like_conv,cmt_conv,dm_conv,bkfl_conv,bkun_conv,
              bio_conv,hash_conv,sched_conv]:
        app.add_handler(c)

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("live",   cmd_live))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(cb))

    # Reply keyboard shortcut handler — must come AFTER conversation handlers
    SHORTCUT_TEXTS = [
        "🏠 Menu","👤 Profile","📊 Stats","📸 Post","📱 Story","🎬 Reel",
        "➕ Follow","❤️ Like","🔍 Search","🔴 Live Status","🧪 Live Test",
        "⚙️ Settings","🔐 Login","ℹ️ About",
    ]
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex("^(" + "|".join(SHORTCUT_TEXTS) + ")$"),
        handle_reply_shortcut
    ))

    print("╔═══════════════════════════════╗")
    print("║   Instagram Pro Bot  v4.0     ║")
    print(f"║   {VERSION:<27}║")
    print("╚═══════════════════════════════╝")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
