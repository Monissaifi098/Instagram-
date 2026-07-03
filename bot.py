"""
╔══════════════════════════════════════════════════╗
║     INSTAGRAM PRO AUTOMATION - TELEGRAM BOT      ║
║  Persistent Login • 2FA OTP • Follow/Unfollow    ║
║  Animations • Live Status • Full Account Control  ║
║  NO AI API REQUIRED — 100% Free to Run           ║
╚══════════════════════════════════════════════════╝

pip install python-telegram-bot instagrapi Pillow requests python-dotenv
"""

import os, asyncio, json, time, random, threading, pickle
from datetime import datetime
from pathlib import Path
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
from instagrapi import Client as InstaClient
from instagrapi.exceptions import (
    TwoFactorRequired, ChallengeRequired, BadPassword, UserNotFound
)
from PIL import Image, ImageDraw, ImageFont
import requests
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ALLOWED_USERS  = list(map(int, os.getenv("ALLOWED_USERS","").split(","))) if os.getenv("ALLOWED_USERS") else []
SESSION_FILE   = "ig_session.pkl"
CREDS_FILE     = "ig_creds.json"
ANALYTICS_FILE = "analytics.json"

# Conversation States
(
    ST_USERNAME, ST_PASSWORD, ST_OTP,
    ST_IMAGE, ST_STORY_TEXT, ST_STORY_IMAGE,
    ST_SCHEDULE_TIME, ST_FOLLOW_USER,
    ST_UNFOLLOW_USER, ST_SEARCH_USER,
    ST_2FA_CODE, ST_CAPTION_INPUT,
    ST_BULK_FOLLOW, ST_COMMENT_TEXT,
    ST_LIKE_COUNT
) = range(15)

# ══════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════
ig_client: InstaClient | None = None
ig_logged_in   = False
ig_username    = ""
scheduled_posts = []
analytics_data  = {
    "posts": [], "stories": [], "reels": [],
    "follows": [], "unfollows": [],
    "likes": [], "comments": []
}

# ══════════════════════════════════════════════════
#  ANIMATION HELPERS
# ══════════════════════════════════════════════════
SPINNERS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

async def anim_login(msg):
    frames = [
        "🔐 Instagram se connect ho raha hoon",
        "🔐 Instagram se connect ho raha hoon •",
        "📡 Server se baat kar raha hoon ••",
        "📡 Server se baat kar raha hoon •••",
        "🔑 Credentials verify ho rahe hain",
        "🔑 Credentials verify ho rahe hain ••",
        "🛡️ Security check ho raha hai •••",
        "🛡️ Almost done...",
    ]
    for f in frames:
        try: await msg.edit_text(f)
        except: pass
        await asyncio.sleep(0.45)

async def anim_success(msg, text: str):
    for s in ["✨", "✨✨", "✨✨✨", f"✅  {text}"]:
        try: await msg.edit_text(s)
        except: pass
        await asyncio.sleep(0.28)

async def anim_loading(msg, label="Loading"):
    for i in range(6):
        try: await msg.edit_text(f"{SPINNERS[i]} {label}{'.' * (i%4)}")
        except: pass
        await asyncio.sleep(0.35)

async def anim_logout(msg):
    for s in ["🔓 Logout ho raha hoon","🗑️ Session clear ho raha hai •","👋 Bye bye Instagram ••","✅ Logout complete!"]:
        try: await msg.edit_text(s)
        except: pass
        await asyncio.sleep(0.45)

# ══════════════════════════════════════════════════
#  AUTH & SESSION
# ══════════════════════════════════════════════════
def is_auth(uid: int) -> bool:
    return not ALLOWED_USERS or uid in ALLOWED_USERS

def save_session():
    if ig_client:
        with open(SESSION_FILE,"wb") as f:
            pickle.dump(ig_client.get_settings(), f)
        with open(CREDS_FILE,"w") as f:
            json.dump({"username": ig_username}, f)

def load_session() -> bool:
    global ig_client, ig_logged_in, ig_username
    if not Path(SESSION_FILE).exists(): return False
    try:
        with open(SESSION_FILE,"rb") as f:
            settings = pickle.load(f)
        creds = {}
        if Path(CREDS_FILE).exists():
            with open(CREDS_FILE) as f: creds = json.load(f)
        ig_client = InstaClient()
        ig_client.set_settings(settings)
        ig_client.login(creds.get("username",""), "")
        ig_logged_in = True
        ig_username  = creds.get("username","")
        return True
    except: return False

def logout_instagram():
    global ig_client, ig_logged_in, ig_username
    try:
        if ig_client: ig_client.logout()
    except: pass
    ig_client = None; ig_logged_in = False; ig_username = ""
    for f in [SESSION_FILE, CREDS_FILE]:
        if Path(f).exists(): Path(f).unlink()

def save_analytics():
    with open(ANALYTICS_FILE,"w") as f:
        json.dump(analytics_data, f, indent=2, default=str)

def load_analytics():
    global analytics_data
    if Path(ANALYTICS_FILE).exists():
        with open(ANALYTICS_FILE) as f:
            analytics_data = json.load(f)

# ══════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════
def main_menu_kb(logged_in: bool) -> InlineKeyboardMarkup:
    if logged_in:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 My Profile",       callback_data="menu_profile"),
             InlineKeyboardButton("🔴 Live Status",      callback_data="menu_live")],
            [InlineKeyboardButton("📤 Post Photo",       callback_data="menu_post"),
             InlineKeyboardButton("📱 Post Story",       callback_data="menu_story")],
            [InlineKeyboardButton("🎬 Post Reel",        callback_data="menu_reel"),
             InlineKeyboardButton("⏰ Schedule Post",    callback_data="menu_schedule")],
            [InlineKeyboardButton("➕ Follow",           callback_data="menu_follow"),
             InlineKeyboardButton("➖ Unfollow",         callback_data="menu_unfollow")],
            [InlineKeyboardButton("❤️ Like Posts",      callback_data="menu_like"),
             InlineKeyboardButton("💬 Comment",          callback_data="menu_comment")],
            [InlineKeyboardButton("🔍 Search User",      callback_data="menu_search"),
             InlineKeyboardButton("👥 Followers",        callback_data="menu_followers")],
            [InlineKeyboardButton("➡️ Following",       callback_data="menu_following"),
             InlineKeyboardButton("📅 Scheduled",        callback_data="menu_scheduled")],
            [InlineKeyboardButton("📊 Analytics",        callback_data="menu_analytics"),
             InlineKeyboardButton("🏷️ My Posts",        callback_data="menu_myposts")],
            [InlineKeyboardButton("🚪 Logout",           callback_data="logout_confirm")],
        ])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Instagram Login",  callback_data="start_login")],
            [InlineKeyboardButton("ℹ️ Help & Commands", callback_data="show_help")],
        ])

# ══════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied."); return
    name = update.effective_user.first_name or "User"
    if ig_logged_in:
        txt = (f"╔══════════════════════════╗\n"
               f"║  📸 INSTAGRAM PRO BOT   ║\n"
               f"╚══════════════════════════╝\n\n"
               f"👋 Welcome back, {name}!\n"
               f"✅ Logged in: @{ig_username}\n\n"
               f"Kya karna hai? 👇")
    else:
        txt = (f"╔══════════════════════════╗\n"
               f"║  📸 INSTAGRAM PRO BOT   ║\n"
               f"╚══════════════════════════╝\n\n"
               f"👋 Hello {name}!\n\n"
               f"🔐 Pehle Instagram login karo")
    await update.message.reply_text(txt, reply_markup=main_menu_kb(ig_logged_in))

# ══════════════════════════════════════════════════
#  LOGIN FLOW
# ══════════════════════════════════════════════════
async def cmd_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id): return
    if ig_logged_in:
        await update.message.reply_text(
            f"✅ Aap already logged in hain (@{ig_username})\n\n"
            f"Logout karne ke liye /logout karo.",
            reply_markup=main_menu_kb(True)
        ); return
    await update.message.reply_text(
        "🔐 Instagram Login\n\nApna username bhejo (@ ke bina):"
    )
    return ST_USERNAME

async def handle_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["ig_username"] = update.message.text.strip().lstrip("@")
    await update.message.reply_text(
        f"✅ Username: @{ctx.user_data['ig_username']}\n\n"
        f"🔑 Ab password bhejo:\n"
        f"_(Security ke liye message delete ho jayega)_",
        parse_mode="Markdown"
    )
    return ST_PASSWORD

async def handle_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global ig_client, ig_logged_in, ig_username
    password = update.message.text.strip()
    try: await update.message.delete()
    except: pass

    ctx.user_data["ig_password"] = password
    msg = await update.message.reply_text("🔐 Login ho raha hai...")
    await anim_login(msg)

    username = ctx.user_data["ig_username"]
    try:
        ig_client = InstaClient()
        ig_client.delay_range = [1, 3]
        ig_client.login(username, password)
        ig_logged_in = True
        ig_username  = username
        save_session()
        await anim_success(msg, f"Login successful! @{username}")
        await asyncio.sleep(0.4)
        await msg.edit_text(
            f"🎉 Login Successful!\n\n"
            f"✅ @{username} account connected\n"
            f"💾 Session save ho gaya\n"
            f"(Dobara login nahi karna padega)\n\n"
            f"Ab kya karna hai? 👇",
            reply_markup=main_menu_kb(True)
        )
        return ConversationHandler.END

    except TwoFactorRequired:
        await msg.edit_text(
            "📲 2FA Code Chahiye!\n\n"
            "Authenticator app ya SMS se\n"
            "6-digit code bhejo:"
        )
        return ST_2FA_CODE

    except ChallengeRequired:
        try:
            ig_client.challenge_resolve(ig_client.last_json)
            await msg.edit_text(
                "📧 Email/SMS Verification!\n\n"
                "Instagram ne code bheja hai.\n"
                "Woh 6-digit OTP yahan bhejo:"
            )
            return ST_OTP
        except Exception as e:
            await msg.edit_text(f"❌ Challenge error: {e}\n\nDobara /login karo")
            return ConversationHandler.END

    except BadPassword:
        await msg.edit_text("❌ Password galat hai!\n\nDobara /login karo")
        return ConversationHandler.END
    except Exception as e:
        await msg.edit_text(f"❌ Login error:\n{str(e)[:200]}\n\nDobara /login karo")
        return ConversationHandler.END

async def handle_2fa_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global ig_logged_in, ig_username
    code = update.message.text.strip().replace(" ","")
    msg  = await update.message.reply_text("🔑 2FA verify ho raha hai...")
    try:
        ig_client.login(
            ctx.user_data.get("ig_username",""),
            ctx.user_data.get("ig_password",""),
            verification_code=code
        )
        ig_logged_in = True
        ig_username  = ctx.user_data.get("ig_username","")
        save_session()
        await anim_success(msg, "2FA verified! Login successful!")
        await asyncio.sleep(0.4)
        await msg.edit_text(
            f"🎉 Login Successful!\n✅ @{ig_username}",
            reply_markup=main_menu_kb(True)
        )
    except Exception as e:
        await msg.edit_text(f"❌ Code galat ya expire.\nDobara /login karo\n\nError: {e}")
    return ConversationHandler.END

async def handle_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global ig_logged_in, ig_username
    otp = update.message.text.strip().replace(" ","")
    msg = await update.message.reply_text("📧 OTP verify ho raha hai...")
    try:
        ig_client.challenge_resolve(ig_client.last_json, otp)
        ig_logged_in = True
        ig_username  = ctx.user_data.get("ig_username","")
        save_session()
        await anim_success(msg, "OTP verified! Login successful!")
        await asyncio.sleep(0.4)
        await msg.edit_text(
            f"🎉 Login Successful!\n✅ @{ig_username}",
            reply_markup=main_menu_kb(True)
        )
    except Exception as e:
        await msg.edit_text(f"❌ OTP galat.\nDobara /login karo\n\nError: {e}")
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  LOGOUT
# ══════════════════════════════════════════════════
async def cmd_logout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id): return
    if not ig_logged_in:
        await update.message.reply_text("⚠️ Aap already logged out hain."); return
    await update.message.reply_text(
        f"🚪 Logout Confirm?\n\n@{ig_username} se logout ho jaoge.\nSession bhi delete ho jayega.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Haan, Logout",callback_data="do_logout"),
             InlineKeyboardButton("❌ Nahi",       callback_data="main_menu")]
        ])
    )

# ══════════════════════════════════════════════════
#  LIVE STATUS
# ══════════════════════════════════════════════════
async def show_live_status(target, edit=False):
    if ig_logged_in and ig_client:
        try:
            user = ig_client.account_info()
            bar  = "🟢 " * 5 + " ONLINE"
            txt  = (f"╔══════════════════════════╗\n"
                    f"║      🔴 LIVE STATUS      ║\n"
                    f"╚══════════════════════════╝\n\n"
                    f"{bar}\n\n"
                    f"👤  @{user.username}\n"
                    f"📛  {user.full_name or 'N/A'}\n\n"
                    f"👥  Followers:  {user.follower_count:,}\n"
                    f"➡️  Following:  {user.following_count:,}\n"
                    f"📸  Posts:      {user.media_count:,}\n"
                    f"🔒  Private:    {'Yes' if user.is_private else 'No'}\n"
                    f"✅  Verified:   {'Yes' if user.is_verified else 'No'}\n\n"
                    f"📝  {(user.biography or 'No bio')[:80]}\n\n"
                    f"🕐  {datetime.now().strftime('%I:%M:%S %p')}\n"
                    f"🌐  Status: Connected ✅")
        except Exception as e:
            txt = f"⚠️ Error: {e}"
    else:
        txt = ("╔══════════════════════════╗\n"
               "║      🔴 LIVE STATUS      ║\n"
               "╚══════════════════════════╝\n\n"
               "🔴 🔴 🔴 🔴 🔴  OFFLINE\n\n"
               "❌ Instagram connected nahi\n\n"
               "👉 /login se connect karo")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh",  callback_data="menu_live"),
         InlineKeyboardButton("🏠 Menu",    callback_data="main_menu")]
    ])
    if edit: await target.edit_message_text(txt, reply_markup=kb)
    else:    await target.reply_text(txt, reply_markup=kb)

# ══════════════════════════════════════════════════
#  PROFILE
# ══════════════════════════════════════════════════
async def show_profile(query):
    if not ig_logged_in:
        await query.edit_message_text("❌ Login nahi ho. /login karo"); return
    msg = await query.edit_message_text("⠋ Profile load ho rahi hai...")
    try:
        user = ig_client.account_info()
        try:
            media   = ig_client.user_medias(ig_client.user_id, 3)
            last_post = media[0].taken_at.strftime('%d %b %Y') if media else 'N/A'
            avg_likes = sum(m.like_count for m in media) // max(len(media),1)
        except: last_post = 'N/A'; avg_likes = 0

        txt = (f"╔══════════════════════════╗\n"
               f"║       👤 MY PROFILE      ║\n"
               f"╚══════════════════════════╝\n\n"
               f"📛  Name: {user.full_name or 'N/A'}\n"
               f"🔖  @{user.username}\n"
               f"🆔  ID: {ig_client.user_id}\n\n"
               f"━━━━━━ 📊 Stats ━━━━━━\n"
               f"👥  Followers:   {user.follower_count:,}\n"
               f"➡️  Following:   {user.following_count:,}\n"
               f"📸  Total Posts: {user.media_count:,}\n"
               f"❤️  Avg Likes:   {avg_likes:,}\n\n"
               f"━━━━━━ ℹ️ Info ━━━━━━\n"
               f"📝  Bio: {(user.biography or 'None')[:100]}\n"
               f"🔗  Web: {user.external_url or 'None'}\n"
               f"🔒  Private: {'Yes' if user.is_private else 'No'}\n"
               f"✅  Verified: {'Yes' if user.is_verified else 'No'}\n"
               f"📅  Last Post: {last_post}")
        await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Analytics", callback_data="menu_analytics"),
             InlineKeyboardButton("🔄 Refresh",   callback_data="menu_profile")],
            [InlineKeyboardButton("🏠 Menu",      callback_data="main_menu")]
        ]))
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ══════════════════════════════════════════════════
#  POST PHOTO
# ══════════════════════════════════════════════════
async def cmd_post_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "📸 Post karo\n\nPhoto upload karo:"
    )
    return ST_IMAGE

async def handle_image_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Photo nahi mili. Dobara bhejo.")
        return ST_IMAGE
    msg = await update.message.reply_text("⠋ Photo process ho rahi hai...")
    try:
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
        else:
            file = await update.message.document.get_file()
        path = f"/tmp/ig_post_{update.effective_user.id}.jpg"
        await file.download_to_drive(path)
        ctx.user_data["image_path"] = path
        await msg.edit_text(
            "✅ Photo ready!\n\nAb caption bhejo\n(ya /skip likhkar bina caption ke post karo):"
        )
        return ST_CAPTION_INPUT
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        return ConversationHandler.END

async def handle_caption_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    caption = "" if update.message.text.strip().lower() == "/skip" else update.message.text.strip()
    ctx.user_data["caption"] = caption
    preview = caption[:200] + "..." if len(caption) > 200 else caption
    await update.message.reply_text(
        f"📸 Ready to post!\n\n"
        f"📝 Caption: {preview or '(No caption)'}\n\n"
        f"Confirm karo:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Post Karo!",  callback_data="confirm_post"),
             InlineKeyboardButton("⏰ Schedule",    callback_data="schedule_this")],
            [InlineKeyboardButton("❌ Cancel",      callback_data="cancel_action")]
        ])
    )
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  POST STORY
# ══════════════════════════════════════════════════
async def cmd_story_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "📱 Story post karo\n\nKya bhejoge?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼️ Image Story",  callback_data="story_type_image"),
             InlineKeyboardButton("✍️ Text Story",   callback_data="story_type_text")],
            [InlineKeyboardButton("❌ Cancel",        callback_data="cancel_action")]
        ])
    )

async def handle_story_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Image bhejo.")
        return ST_STORY_IMAGE
    msg = await update.message.reply_text("📱 Story upload ho rahi hai...")
    try:
        file = await (update.message.photo[-1] if update.message.photo else update.message.document).get_file()
        path = f"/tmp/ig_story_{update.effective_user.id}.jpg"
        await file.download_to_drive(path)
        await anim_loading(msg, "Story upload")
        ig_client.photo_upload_to_story(path)
        analytics_data["stories"].append({"type":"image","posted_at":str(datetime.now())})
        save_analytics()
        await msg.edit_text("✅ Story post ho gayi!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]]))
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    return ConversationHandler.END

async def handle_story_text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg  = await update.message.reply_text("🎨 Story image ban rahi hai...")
    colors = [(20,20,80),(60,10,50),(10,60,40),(50,10,70),(80,30,10),(10,40,80)]
    bg = random.choice(colors)
    img  = Image.new("RGB",(1080,1920),color=bg)
    draw = ImageDraw.Draw(img)
    # gradient
    for y in range(1920):
        factor = y / 1920
        r = int(bg[0] + (min(bg[0]+80,255)-bg[0]) * factor)
        g = int(bg[1] + (min(bg[1]+80,255)-bg[1]) * factor)
        b = int(bg[2] + (min(bg[2]+80,255)-bg[2]) * factor)
        draw.line([(0,y),(1080,y)], fill=(r,g,b))
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except:
        font_large = ImageFont.load_default()
        font_small = font_large
    # Word wrap
    words = text.split()
    lines, ln = [], ""
    for w in words:
        test = f"{ln} {w}".strip()
        if draw.textlength(test, font=font_large) < 950: ln = test
        else: lines.append(ln); ln = w
    lines.append(ln)
    total_h = len(lines) * 90
    y = (1920 - total_h) // 2
    for l in lines:
        w2 = draw.textlength(l, font=font_large)
        draw.text(((1080-w2)/2+3, y+3), l, fill=(0,0,0,100), font=font_large)
        draw.text(((1080-w2)/2, y), l, fill="white", font=font_large)
        y += 90
    # Watermark
    wm = f"@{ig_username}"
    ww = draw.textlength(wm, font=font_small)
    draw.text(((1080-ww)/2, 1820), wm, fill=(255,255,255,150), font=font_small)

    buf = BytesIO(); img.save(buf,"JPEG",quality=95); buf.seek(0)
    await anim_loading(msg, "Story upload")
    try:
        ig_client.photo_upload_to_story(buf)
        analytics_data["stories"].append({"text":text,"posted_at":str(datetime.now())})
        save_analytics()
        await msg.edit_text("✅ Story post ho gayi!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]]))
    except Exception as e:
        await msg.edit_text(f"❌ Upload error: {e}")
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  REEL UPLOAD
# ══════════════════════════════════════════════════
async def cmd_reel_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("🎬 Reel upload karo\n\nVideo file bhejo (MP4 format):")
    return ST_STORY_IMAGE  # reusing state for video

async def handle_reel_upload(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.video and not update.message.document:
        await update.message.reply_text("❌ Video file bhejo.")
        return ST_STORY_IMAGE
    msg = await update.message.reply_text("⠋ Reel upload ho rahi hai...")
    try:
        file = await (update.message.video or update.message.document).get_file()
        path = f"/tmp/ig_reel_{update.effective_user.id}.mp4"
        await file.download_to_drive(path)
        caption = ctx.user_data.get("caption","")
        await anim_loading(msg,"Reel uploading")
        ig_client.clip_upload(path, caption=caption)
        analytics_data["reels"].append({"posted_at":str(datetime.now())})
        save_analytics()
        await msg.edit_text("✅ Reel post ho gaya!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]]))
    except Exception as e:
        await msg.edit_text(f"❌ Reel error: {e}\n\nNote: Video 3-60 sec hona chahiye")
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  FOLLOW / UNFOLLOW
# ══════════════════════════════════════════════════
async def cmd_follow_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("➕ Kis user ko follow karna hai?\n\nUsername bhejo:")
    return ST_FOLLOW_USER

async def handle_follow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip("@")
    msg = await update.message.reply_text(f"⠋ @{username} dhundh raha hoon...")
    try:
        uid = ig_client.user_id_from_username(username)
        for f in [f"⠋ @{username} mila!",f"⠙ Follow request bhej raha hai...",f"⠹ Almost done...","✅ Done!"]:
            await msg.edit_text(f); await asyncio.sleep(0.4)
        ig_client.user_follow(uid)
        analytics_data["follows"].append({"username":username,"at":str(datetime.now())})
        save_analytics()
        await msg.edit_text(
            f"✅ @{username} ko follow kar liya!\n\nFollow request send ho gayi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Search More", callback_data="menu_search"),
                 InlineKeyboardButton("🏠 Menu",        callback_data="main_menu")]
            ])
        )
    except UserNotFound:
        await msg.edit_text(f"❌ @{username} Instagram pe nahi mila.")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    return ConversationHandler.END

async def cmd_unfollow_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("➖ Kis user ko unfollow karna hai?\n\nUsername bhejo:")
    return ST_UNFOLLOW_USER

async def handle_unfollow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip("@")
    msg = await update.message.reply_text(f"⠋ @{username} unfollow ho raha hai...")
    try:
        uid = ig_client.user_id_from_username(username)
        for f in [f"⠙ Unfollow request bhej raha hai...","⠹ Processing...","✅ Done!"]:
            await msg.edit_text(f); await asyncio.sleep(0.4)
        ig_client.user_unfollow(uid)
        analytics_data["unfollows"].append({"username":username,"at":str(datetime.now())})
        save_analytics()
        await msg.edit_text(
            f"✅ @{username} ko unfollow kar diya!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]])
        )
    except UserNotFound:
        await msg.edit_text(f"❌ @{username} nahi mila.")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  SEARCH USER
# ══════════════════════════════════════════════════
async def cmd_search_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text("🔍 User search karo\n\nUsername bhejo:")
    return ST_SEARCH_USER

async def handle_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip("@")
    msg = await update.message.reply_text(f"🔍 @{username} dhundh raha hoon...")
    try:
        uid  = ig_client.user_id_from_username(username)
        info = ig_client.user_info(uid)
        try:
            fr   = ig_client.user_friendship(uid)
            i_follow  = "✅ Yes" if fr.following  else "❌ No"
            they_follow = "✅ Yes" if fr.followed_by else "❌ No"
        except: i_follow = they_follow = "N/A"

        txt = (f"🔍 @{username}\n\n"
               f"📛  Name: {info.full_name or 'N/A'}\n"
               f"🆔  ID: {uid}\n"
               f"👥  Followers: {info.follower_count:,}\n"
               f"➡️  Following: {info.following_count:,}\n"
               f"📸  Posts: {info.media_count:,}\n"
               f"🔒  Private: {'Yes' if info.is_private else 'No'}\n"
               f"✅  Verified: {'Yes' if info.is_verified else 'No'}\n\n"
               f"🤝 Relationship:\n"
               f"   You follow them: {i_follow}\n"
               f"   They follow you: {they_follow}\n\n"
               f"📝 Bio: {(info.biography or 'None')[:100]}")

        is_following = "✅" in i_follow
        f_btn = InlineKeyboardButton("➖ Unfollow", callback_data=f"q_unfollow_{username}") if is_following else \
                InlineKeyboardButton("➕ Follow",   callback_data=f"q_follow_{username}")
        await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup([
            [f_btn, InlineKeyboardButton("📸 Posts", callback_data=f"user_posts_{username}")],
            [InlineKeyboardButton("🏠 Menu", callback_data="main_menu")]
        ]))
    except UserNotFound:
        await msg.edit_text(f"❌ @{username} nahi mila.")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  LIKE POSTS
# ══════════════════════════════════════════════════
async def cmd_like_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "❤️ Like karo\n\nUsername bhejo (us user ki last posts like hongi):"
    )
    return ST_LIKE_COUNT

async def handle_like(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip("@")
    msg = await update.message.reply_text(f"❤️ @{username} ki posts like ho rahi hain...")
    try:
        uid   = ig_client.user_id_from_username(username)
        media = ig_client.user_medias(uid, 5)
        liked = 0
        for m in media[:5]:
            try:
                ig_client.media_like(m.id)
                liked += 1
                await asyncio.sleep(random.uniform(1.5,3))
            except: pass
        analytics_data["likes"].append({"username":username,"count":liked,"at":str(datetime.now())})
        save_analytics()
        await msg.edit_text(
            f"✅ @{username} ki {liked} posts like ho gayi!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]])
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  COMMENT
# ══════════════════════════════════════════════════
async def cmd_comment_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo"); return
    await update.message.reply_text(
        "💬 Comment karo\n\nFormat: username | comment text\nExample: john_doe | Great photo! 🔥"
    )
    return ST_COMMENT_TEXT

async def handle_comment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split("|", 1)
    if len(parts) != 2:
        await update.message.reply_text("❌ Format galat.\n\nUsername | Comment text\nExample: john_doe | Nice pic!")
        return ST_COMMENT_TEXT
    username, comment = parts[0].strip().lstrip("@"), parts[1].strip()
    msg = await update.message.reply_text(f"💬 @{username} ki latest post pe comment ho raha hai...")
    try:
        uid   = ig_client.user_id_from_username(username)
        media = ig_client.user_medias(uid, 1)
        if not media:
            await msg.edit_text(f"❌ @{username} ki koi post nahi mili.")
            return ConversationHandler.END
        ig_client.media_comment(media[0].id, comment)
        analytics_data["comments"].append({"username":username,"comment":comment,"at":str(datetime.now())})
        save_analytics()
        await msg.edit_text(
            f"✅ Comment ho gaya!\n\n@{username} ki post pe:\n\"{comment}\"",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]])
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
    return ConversationHandler.END

# ══════════════════════════════════════════════════
#  FOLLOWERS / FOLLOWING
# ══════════════════════════════════════════════════
async def show_followers(query):
    msg = await query.edit_message_text("⠋ Followers load ho rahe hain...")
    try:
        fl = ig_client.user_followers(ig_client.user_id, amount=20)
        txt = f"👥 Aapke Followers (Top 20):\n\n"
        for i,(uid,u) in enumerate(list(fl.items())[:20],1):
            v = "✅" if u.is_verified else ""
            txt += f"{i:2}. @{u.username} {v}\n"
        await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Following", callback_data="menu_following"),
             InlineKeyboardButton("🔄 Refresh",   callback_data="menu_followers")],
            [InlineKeyboardButton("🏠 Menu",      callback_data="main_menu")]
        ]))
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

async def show_following(query):
    msg = await query.edit_message_text("⠋ Following list load ho rahi hai...")
    try:
        fl = ig_client.user_following(ig_client.user_id, amount=20)
        txt = "➡️ Aap Jinhe Follow Karte Ho (Top 20):\n\n"
        for i,(uid,u) in enumerate(list(fl.items())[:20],1):
            txt += f"{i:2}. @{u.username}\n"
        await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Followers", callback_data="menu_followers"),
             InlineKeyboardButton("🔄 Refresh",   callback_data="menu_following")],
            [InlineKeyboardButton("🏠 Menu",      callback_data="main_menu")]
        ]))
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ══════════════════════════════════════════════════
#  MY POSTS
# ══════════════════════════════════════════════════
async def show_my_posts(query):
    msg = await query.edit_message_text("⠋ Posts load ho rahi hain...")
    try:
        media = ig_client.user_medias(ig_client.user_id, 10)
        if not media:
            await msg.edit_text("📭 Koi post nahi hai."); return
        txt = "📸 Aapki Last 10 Posts:\n\n"
        for i,m in enumerate(media,1):
            likes = m.like_count or 0
            cmts  = m.comment_count or 0
            date  = m.taken_at.strftime('%d %b %Y') if m.taken_at else 'N/A'
            cap   = (m.caption_text or "No caption")[:40]
            txt  += f"{i}. {date}\n   ❤️ {likes:,}  💬 {cmts:,}\n   _{cap}_\n\n"
        await msg.edit_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="menu_myposts"),
             InlineKeyboardButton("🏠 Menu",    callback_data="main_menu")]
        ]))
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ══════════════════════════════════════════════════
#  ANALYTICS
# ══════════════════════════════════════════════════
async def show_analytics(query):
    msg = await query.edit_message_text("📊 Analytics load ho raha hai...")
    load_analytics()
    posts    = len(analytics_data.get("posts",[]))
    stories  = len(analytics_data.get("stories",[]))
    reels    = len(analytics_data.get("reels",[]))
    follows  = len(analytics_data.get("follows",[]))
    unflw    = len(analytics_data.get("unfollows",[]))
    likes    = len(analytics_data.get("likes",[]))
    comments = len(analytics_data.get("comments",[]))
    pending  = sum(1 for p in scheduled_posts if p.get("status")=="scheduled")

    ig_extra = ""
    if ig_logged_in and ig_client:
        try:
            u = ig_client.account_info()
            ig_extra = (f"\n━━━ 📈 Live IG Stats ━━━\n"
                        f"👥  Followers:  {u.follower_count:,}\n"
                        f"➡️  Following:  {u.following_count:,}\n"
                        f"📸  Posts:      {u.media_count:,}")
        except: pass

    txt = (f"╔══════════════════════════╗\n"
           f"║    📊 BOT ANALYTICS      ║\n"
           f"╚══════════════════════════╝\n\n"
           f"━━━ 📤 Content Posted ━━━\n"
           f"📸  Photos:   {posts}\n"
           f"📱  Stories:  {stories}\n"
           f"🎬  Reels:    {reels}\n"
           f"⏳  Pending:  {pending}\n\n"
           f"━━━ 🤝 Social Actions ━━━\n"
           f"➕  Follows:   {follows}\n"
           f"➖  Unfollows: {unflw}\n"
           f"❤️  Likes:     {likes}\n"
           f"💬  Comments:  {comments}"
           f"{ig_extra}")
    await msg.edit_text(txt, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_analytics"),
         InlineKeyboardButton("🏠 Menu",    callback_data="main_menu")]
    ]))

# ══════════════════════════════════════════════════
#  SCHEDULE
# ══════════════════════════════════════════════════
async def cmd_schedule_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ig_logged_in:
        await update.message.reply_text("❌ Pehle /login karo\nPhir /post se image upload karo"); return
    if not ctx.user_data.get("image_path"):
        await update.message.reply_text("❌ Pehle /post se image upload karo, phir schedule karo."); return
    await update.message.reply_text(
        "⏰ Schedule Time Batao:\n\nFormat: DD/MM/YYYY HH:MM\nExample: 25/07/2025 20:30"
    )
    return ST_SCHEDULE_TIME

async def handle_schedule_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        st   = datetime.strptime(update.message.text.strip(), "%d/%m/%Y %H:%M")
        img  = ctx.user_data.get("image_path","")
        cap  = ctx.user_data.get("caption","")
        pid  = len(scheduled_posts)+1
        scheduled_posts.append({"id":pid,"time":st,"image_path":img,"caption":cap,"status":"scheduled"})
        delay = (st - datetime.now()).total_seconds()
        if delay > 0:
            threading.Timer(delay, lambda: asyncio.run(_exec_sched(pid,img,cap))).start()
        await update.message.reply_text(
            f"✅ Post Schedule Ho Gaya!\n\n"
            f"📅 {st.strftime('%d %b %Y, %I:%M %p')}\n"
            f"🆔 Post ID: #{pid}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 All Scheduled",callback_data="menu_scheduled"),
                 InlineKeyboardButton("🏠 Menu",         callback_data="main_menu")]
            ])
        )
    except ValueError:
        await update.message.reply_text("❌ Format galat.\n\nUse: DD/MM/YYYY HH:MM\nExample: 25/07/2025 20:30")
    return ConversationHandler.END

async def _exec_sched(pid, img, cap):
    try:
        ig_client.photo_upload(img, caption=cap)
        analytics_data["posts"].append({"caption":cap[:80],"posted_at":str(datetime.now())})
        save_analytics()
        for p in scheduled_posts:
            if p["id"]==pid: p["status"]="posted"
    except:
        for p in scheduled_posts:
            if p["id"]==pid: p["status"]="failed"

async def show_scheduled(query):
    if not scheduled_posts:
        await query.edit_message_text("📭 Koi scheduled post nahi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]])); return
    txt = "📅 Scheduled Posts:\n\n"
    for p in scheduled_posts[-10:]:
        e = {"scheduled":"⏳","posted":"✅","failed":"❌","cancelled":"🚫"}.get(p["status"],"❓")
        t = p["time"].strftime('%d %b %Y %I:%M %p') if isinstance(p["time"],datetime) else str(p["time"])
        txt += f"{e} #{p['id']} — {t}\n"
    await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_scheduled"),
         InlineKeyboardButton("🏠 Menu",    callback_data="main_menu")]
    ]))

# ══════════════════════════════════════════════════
#  MAIN CALLBACK ROUTER
# ══════════════════════════════════════════════════
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d = query.data

    if d == "main_menu":
        name = query.from_user.first_name or "User"
        txt = (f"📸 INSTAGRAM PRO BOT\n\n"
               f"👋 {name}\n"
               f"{'✅ Logged in: @'+ig_username if ig_logged_in else '❌ Not logged in'}")
        await query.edit_message_text(txt, reply_markup=main_menu_kb(ig_logged_in))

    elif d == "start_login":
        await query.edit_message_text("🔐 Instagram Login\n\nApna username bhejo (@ ke bina):")
        ctx.user_data["cb_login"] = True

    elif d == "show_help":
        txt = ("📖 Commands Guide\n\n"
               "/start — Main menu\n"
               "/login — Instagram login\n"
               "/logout — Logout\n"
               "/post — Photo post karo\n"
               "/story — Story post karo\n"
               "/reel — Reel upload karo\n"
               "/follow — Follow karo\n"
               "/unfollow — Unfollow karo\n"
               "/search — User search\n"
               "/like — Last 5 posts like karo\n"
               "/comment — Comment karo\n"
               "/schedule — Post schedule karo\n"
               "/live — Live status\n"
               "/analytics — Stats\n"
               "/cancel — Cancel")
        await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]]))

    elif d == "logout_confirm":
        await query.edit_message_text(
            f"🚪 Logout Confirm?\n\n@{ig_username} se logout hoge.\nSession delete ho jayega.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Logout Karo",callback_data="do_logout"),
                 InlineKeyboardButton("❌ Nahi",       callback_data="main_menu")]
            ])
        )

    elif d == "do_logout":
        await query.edit_message_text("🔓 Logout ho raha hoon...")
        await anim_logout(query.message)
        logout_instagram()
        await query.message.edit_text(
            "👋 Logout Ho Gaya!\n\nSession clear.\nDobara /login karo.",
            reply_markup=main_menu_kb(False)
        )

    elif d == "menu_live":      await show_live_status(query, edit=True)
    elif d == "menu_profile":   await show_profile(query)
    elif d == "menu_analytics": await show_analytics(query)
    elif d == "menu_followers": await show_followers(query)
    elif d == "menu_following": await show_following(query)
    elif d == "menu_scheduled": await show_scheduled(query)
    elif d == "menu_myposts":   await show_my_posts(query)

    elif d in ["menu_post","menu_story","menu_reel","menu_follow",
               "menu_unfollow","menu_search","menu_like","menu_comment","menu_schedule"]:
        cmds = {"menu_post":"/post","menu_story":"/story","menu_reel":"/reel",
                "menu_follow":"/follow","menu_unfollow":"/unfollow","menu_search":"/search",
                "menu_like":"/like","menu_comment":"/comment","menu_schedule":"/schedule"}
        await query.edit_message_text(
            f"👉 {cmds[d]} command bhejo ya type karo:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]])
        )

    elif d == "story_type_image":
        await query.edit_message_text("🖼️ Story ke liye image bhejo:")
        ctx.user_data["story_type"] = "image"

    elif d == "story_type_text":
        await query.edit_message_text("✍️ Story text bhejo (bot image banake post karega):")
        ctx.user_data["story_type"] = "text"

    elif d == "confirm_post":
        img = ctx.user_data.get("image_path","")
        cap = ctx.user_data.get("caption","")
        if not img:
            await query.edit_message_text("❌ Image nahi mili. /post se dobara try karo."); return
        await query.edit_message_text("⠋ Post ho rahi hai...")
        await anim_loading(query.message, "Uploading to Instagram")
        try:
            ig_client.photo_upload(img, caption=cap)
            analytics_data["posts"].append({"caption":cap[:80],"posted_at":str(datetime.now())})
            save_analytics()
            await anim_success(query.message, "Post successfully upload ho gaya!")
        except Exception as e:
            await query.message.edit_text(f"❌ Error: {e}")

    elif d == "schedule_this":
        await query.edit_message_text(
            "⏰ Schedule Time:\n\nFormat: DD/MM/YYYY HH:MM\nExample: 25/07/2025 20:30"
        )
        ctx.user_data["scheduling"] = True

    elif d == "cancel_action":
        ctx.user_data.clear()
        await query.edit_message_text("❌ Cancelled.", reply_markup=main_menu_kb(ig_logged_in))

    elif d.startswith("q_follow_"):
        username = d.replace("q_follow_","")
        await query.edit_message_text(f"➕ @{username} follow ho raha hai...")
        try:
            uid = ig_client.user_id_from_username(username)
            ig_client.user_follow(uid)
            analytics_data["follows"].append({"username":username,"at":str(datetime.now())})
            save_analytics()
            await query.message.edit_text(f"✅ @{username} follow ho gaya!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]]))
        except Exception as e:
            await query.message.edit_text(f"❌ Error: {e}")

    elif d.startswith("q_unfollow_"):
        username = d.replace("q_unfollow_","")
        await query.edit_message_text(f"➖ @{username} unfollow ho raha hai...")
        try:
            uid = ig_client.user_id_from_username(username)
            ig_client.user_unfollow(uid)
            analytics_data["unfollows"].append({"username":username,"at":str(datetime.now())})
            save_analytics()
            await query.message.edit_text(f"✅ @{username} unfollow ho gaya!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]]))
        except Exception as e:
            await query.message.edit_text(f"❌ Error: {e}")

    elif d.startswith("user_posts_"):
        username = d.replace("user_posts_","")
        await query.edit_message_text(f"⠋ @{username} ki posts load ho rahi hain...")
        try:
            uid   = ig_client.user_id_from_username(username)
            media = ig_client.user_medias(uid, 6)
            txt   = f"📸 @{username} ki Last Posts:\n\n"
            for i,m in enumerate(media,1):
                date = m.taken_at.strftime('%d %b %Y') if m.taken_at else 'N/A'
                txt += f"{i}. {date} — ❤️{m.like_count or 0:,} 💬{m.comment_count or 0:,}\n"
            await query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="main_menu")]]))
        except Exception as e:
            await query.message.edit_text(f"❌ Error: {e}")

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancelled.", reply_markup=main_menu_kb(ig_logged_in))
    return ConversationHandler.END

async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update.effective_user.id): return
    await show_live_status(update.message)

# ══════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════
def main():
    load_analytics()
    if load_session():
        print(f"✅ Session restored: @{ig_username}")
    else:
        print("ℹ️  No session. /login required.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Login conversation
    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            ST_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username)],
            ST_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)],
            ST_2FA_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_2fa_code)],
            ST_OTP:      [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_otp)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Post photo conversation
    post_conv = ConversationHandler(
        entry_points=[CommandHandler("post", cmd_post_ask)],
        states={
            ST_IMAGE:         [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image_upload)],
            ST_CAPTION_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_caption_input)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Story conversation
    story_conv = ConversationHandler(
        entry_points=[CommandHandler("story", cmd_story_ask)],
        states={
            ST_STORY_TEXT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_story_text_input)],
            ST_STORY_IMAGE: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_story_image)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Reel conversation
    reel_conv = ConversationHandler(
        entry_points=[CommandHandler("reel", cmd_reel_ask)],
        states={
            ST_STORY_IMAGE: [MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_reel_upload)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Follow conversation
    follow_conv = ConversationHandler(
        entry_points=[CommandHandler("follow", cmd_follow_ask)],
        states={ST_FOLLOW_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_follow)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Unfollow conversation
    unfollow_conv = ConversationHandler(
        entry_points=[CommandHandler("unfollow", cmd_unfollow_ask)],
        states={ST_UNFOLLOW_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unfollow)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Search conversation
    search_conv = ConversationHandler(
        entry_points=[CommandHandler("search", cmd_search_ask)],
        states={ST_SEARCH_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Like conversation
    like_conv = ConversationHandler(
        entry_points=[CommandHandler("like", cmd_like_ask)],
        states={ST_LIKE_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_like)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Comment conversation
    comment_conv = ConversationHandler(
        entry_points=[CommandHandler("comment", cmd_comment_ask)],
        states={ST_COMMENT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    # Schedule conversation
    schedule_conv = ConversationHandler(
        entry_points=[CommandHandler("schedule", cmd_schedule_ask)],
        states={ST_SCHEDULE_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_schedule_time)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)]
    )

    for conv in [login_conv, post_conv, story_conv, reel_conv, follow_conv,
                 unfollow_conv, search_conv, like_conv, comment_conv, schedule_conv]:
        app.add_handler(conv)

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("logout",    cmd_logout))
    app.add_handler(CommandHandler("live",      cmd_live))
    app.add_handler(CommandHandler("analytics", lambda u,c: asyncio.create_task(show_analytics_cmd(u,c))))
    app.add_handler(CommandHandler("cancel",    cmd_cancel))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("╔══════════════════════════╗")
    print("║  Instagram Pro Bot LIVE  ║")
    print("╚══════════════════════════╝")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

async def show_analytics_cmd(update: Update, ctx):
    if not is_auth(update.effective_user.id): return
    msg = await update.message.reply_text("📊 Loading...")
    class FQ:
        message = msg
        async def edit_message_text(self,*a,**kw): await msg.edit_text(*a,**kw)
    await show_analytics(FQ())

if __name__ == "__main__":
    main()
