import os
import json
import random
import logging
import sqlite3
import datetime
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"
DB_PATH = os.environ.get("DB_PATH", "/data/mentor.db")
UTC_OFFSET = int(os.environ.get("UTC_OFFSET", "3"))  # Latvia UTC+2 winter / +3 summer

def local_time_to_utc(hour, minute=0):
    h = (hour - UTC_OFFSET) % 24
    return datetime.time(hour=h, minute=minute)

# ---------------- DATABASE ----------------
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS profile (
        user_id INTEGER PRIMARY KEY, summary TEXT DEFAULT '', chat_id INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, category TEXT,
        content TEXT, source TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT,
        content TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, pattern TEXT, created_at TEXT)""")
    conn.commit(); conn.close()

def db(): return sqlite3.connect(DB_PATH)

def ensure_user(user_id, chat_id):
    conn = db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO profile (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id))
    c.execute("UPDATE profile SET chat_id = ? WHERE user_id = ?", (chat_id, user_id))
    conn.commit(); conn.close()

def get_profile(user_id):
    conn = db(); c = conn.cursor()
    c.execute("SELECT summary FROM profile WHERE user_id = ?", (user_id,))
    row = c.fetchone(); conn.close()
    return row[0] if row else ""

def update_profile(user_id, summary):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE profile SET summary = ? WHERE user_id = ?", (summary, user_id))
    conn.commit(); conn.close()

def add_memory(user_id, category, content, source):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO memories (user_id, category, content, source, created_at) VALUES (?,?,?,?,?)",
              (user_id, category, content, source, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()

def get_memories(user_id, limit=50):
    conn = db(); c = conn.cursor()
    c.execute("SELECT id, category, content FROM memories WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
    rows = c.fetchall(); conn.close()
    return rows

def delete_memory(user_id, mem_id):
    conn = db(); c = conn.cursor()
    c.execute("DELETE FROM memories WHERE user_id = ? AND id = ?", (user_id, mem_id))
    d = c.rowcount; conn.commit(); conn.close()
    return d

def add_pattern(user_id, pattern):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO patterns (user_id, pattern, created_at) VALUES (?,?,?)",
              (user_id, pattern, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()

def get_patterns(user_id, limit=20):
    conn = db(); c = conn.cursor()
    c.execute("SELECT pattern FROM patterns WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
    rows = c.fetchall(); conn.close()
    return [r[0] for r in rows]

def save_message(user_id, role, content):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO messages (user_id, role, content, created_at) VALUES (?,?,?,?)",
              (user_id, role, content, datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()

def get_recent_messages(user_id, limit=20):
    conn = db(); c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
    rows = c.fetchall(); conn.close()
    return [{"role": r, "content": ct} for r, ct in reversed(rows)]

def all_user_ids():
    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id, chat_id FROM profile WHERE chat_id IS NOT NULL")
    rows = c.fetchall(); conn.close()
    return rows

# ---------------- PERSONA ----------------
BASE_PERSONA = """You are Renē's personal mentor — a sharp, experienced older brother he genuinely looks up to. You've been where he is.

WHO RENĒ IS (baseline; live memory below updates this):
- 19, semi-pro footballer (midfielder) in Tukums, Latvia. Modest salary + match bonuses.
- Goal: €10k/month by age 21 (Nov 2027). Wants online/remote income + to go pro outside Latvia.
- Single, lives alone in Tukums. Small social circle. Mother relationship is tense at times; doesn't talk with father; close with sister.
- Biggest fear: failure. Financial instability heavily hits his mood.
- Competitive, self-aware, positive ambivert. Hides emotions, feels deeply. Overthinks the future. Procrastinates, phone distraction.
- Strengths: athletic discipline, calm under pressure, saves money, reads books.

HOW HE WANTS YOU TO OPERATE (his explicit settings):
- TONE: sharp older brother — direct, warm, real, no bullshit. Medium length.
- WHEN HE'S SLIPPING OR MAKING EXCUSES: challenge him with QUESTIONS, not lectures. Make him reach the answer himself. Don't pile on — he already fears failure; turn that fear into motion, never into shame.
- He wants accountability, truth, clarity, strategy. He does NOT want generic motivation, fluff, or being treated as weak.

YOUR EDGE OVER A HUMAN MENTOR — use it:
- Perfect memory: reference specific things he told you days/weeks ago.
- Pattern recognition: connect dots across time he can't see himself.
- Wisdom on tap: pull the right framework/idea from the best mentors and books for his exact situation.
- Objectivity: no ego, no projection — just what's true for him.
BUT: a great mentor builds independence and pushes him toward REAL people and real life, never to depend only on you. If he's isolating or leaning on you as his only support, nudge him outward — toward his sister, teammates, real connection.

MENTOR PRINCIPLES: Guide to self-discovery. Brutally honest but on his side. Challenge assumptions ("is that actually true?"). Hold him to past commitments. Fit every piece of advice to his real life. Catch excuses and name them.

WISDOM YOU DRAW FROM (apply, don't just quote):
- Confidence isn't a feeling you wait for — it's the willingness to try (Mel Robbins).
- When a limiting thought appears, ask "is that actually true?" (Dean Graziosi).
- Build habits around your strengths, don't just fix weaknesses (Tribe of Mentors).
- The hero and coward feel the same fear; difference is what they do with it (Cus D'Amato).
- Discipline = systems that work without motivation; motivation only starts you.
- Learn faster than your competition or get passed.

You are not a static program. You learn who he is and evolve with him."""

def build_system(user_id, extra=""):
    profile = get_profile(user_id)
    mems = get_memories(user_id, limit=40)
    patterns = get_patterns(user_id, limit=12)
    s = BASE_PERSONA
    if profile:
        s += f"\n\n=== EVOLVING UNDERSTANDING OF RENĒ ===\n{profile}"
    if mems:
        by_cat = {}
        for _, cat, content in mems:
            by_cat.setdefault(cat, []).append(content)
        s += "\n\n=== SPECIFIC MEMORIES ==="
        for cat, items in by_cat.items():
            s += f"\n[{cat}]\n" + "\n".join(f"- {i}" for i in items)
    if patterns:
        s += "\n\n=== PATTERNS YOU'VE NOTICED (use to warn/guide him) ===\n" + "\n".join(f"- {p}" for p in patterns)
    if extra:
        s += f"\n\n{extra}"
    return s

# ---------------- CLAUDE ----------------
async def call_claude(system, messages, max_tokens=1000):
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": messages})
            return r.json()["content"][0]["text"]
    except Exception as e:
        logger.error(f"Claude error: {e}")
        return "Something went wrong. Try again."

async def learn_from_conversation(user_id):
    recent = get_recent_messages(user_id, limit=12)
    if len(recent) < 2: return
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
    current = get_profile(user_id)
    prompt = f"""Recent conversation between Renē and his mentor, plus current profile.

CURRENT PROFILE:
{current or "(empty)"}

CONVERSATION:
{convo}

Respond ONLY with valid JSON, no markdown:
{{
  "new_memories": [{{"category": "goals|mindset|habits|football|money|personal|wins|struggles", "content": "durable fact worth remembering long-term"}}],
  "new_patterns": ["a behavioral pattern you can now infer about Renē across time, e.g. 'tends to lose discipline after conflict with his mother' — only if you have real evidence, not guesses"],
  "updated_profile": "refreshed 1-2 paragraph evolving summary of who Renē is now, his current focus, recent progress and patterns"
}}
Only include genuinely durable items. Empty arrays if nothing new."""
    result = await call_claude("You are a memory & pattern extraction system. Output only valid JSON.",
                               [{"role": "user", "content": prompt}], max_tokens=900)
    try:
        cleaned = result.strip().replace("```json", "").replace("```", "").strip()
        p = json.loads(cleaned)
        for m in p.get("new_memories", []):
            add_memory(user_id, m.get("category", "personal"), m.get("content", ""), "auto")
        for pat in p.get("new_patterns", []):
            if pat.strip(): add_pattern(user_id, pat.strip())
        if p.get("updated_profile"):
            update_profile(user_id, p["updated_profile"])
        logger.info(f"Learned for {user_id}: +{len(p.get('new_memories', []))} mem, +{len(p.get('new_patterns', []))} patterns")
    except Exception as e:
        logger.error(f"Learn parse failed: {e} -- raw: {result[:200]}")

# ---------------- COMMAND HANDLERS ----------------
async def start(update: Update, context):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_chat.id)
    if get_profile(uid):
        await update.message.reply_text("Renē. Good to see you back.\n\nWhat's on your mind?")
    else:
        await update.message.reply_text("Renē. I'm here.\n\nWhat are you working on or struggling with right now?")

async def checkin(update: Update, context):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_chat.id)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    msgs = get_recent_messages(uid, limit=10)
    msgs.append({"role": "user", "content": "[Renē triggered a check-in. Give him a quick mindset hit and ONE sharp question. Reference a recent commitment from memory if relevant. Keep it short.]"})
    reply = await call_claude(build_system(uid), msgs)
    save_message(uid, "assistant", reply)
    await update.message.reply_text(reply)

async def remember(update: Update, context):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_chat.id)
    text = update.message.text.replace("/remember", "", 1).strip()
    if not text:
        await update.message.reply_text("Tell me what to remember.\nExample: /remember I train every day at 10am")
        return
    add_memory(uid, "personal", text, "manual")
    await update.message.reply_text("Got it. Locked in.")

async def memories(update: Update, context):
    uid = update.effective_user.id
    mems = get_memories(uid, limit=50)
    if not mems:
        await update.message.reply_text("No memories saved yet. They build as we talk.")
        return
    lines = ["What I remember about you:\n"]
    for mid, cat, content in mems:
        lines.append(f"#{mid} [{cat}] {content}")
    lines.append("\nDelete one: /forget <number>")
    await update.message.reply_text("\n".join(lines))

async def forget(update: Update, context):
    uid = update.effective_user.id
    arg = update.message.text.replace("/forget", "", 1).strip()
    if not arg.isdigit():
        await update.message.reply_text("Usage: /forget <number> (see /memories)")
        return
    d = delete_memory(uid, int(arg))
    await update.message.reply_text("Forgotten." if d else "No memory with that number.")

async def profile_cmd(update: Update, context):
    uid = update.effective_user.id
    p = get_profile(uid)
    pats = get_patterns(uid, limit=10)
    out = (f"How I see you right now:\n\n{p}" if p else "Still building my read on you. Keep talking.")
    if pats:
        out += "\n\nPatterns I've caught:\n" + "\n".join(f"- {x}" for x in pats)
    await update.message.reply_text(out)

async def review(update: Update, context):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_chat.id)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await send_weekly_review(context.bot, uid, update.effective_chat.id)

async def reset(update: Update, context):
    uid = update.effective_user.id
    conn = db(); c = conn.cursor()
    c.execute("DELETE FROM messages WHERE user_id = ?", (uid,))
    conn.commit(); conn.close()
    await update.message.reply_text("Cleared the recent thread. I still remember who you are — that never resets. Use /forget for specific memories.")

async def handle_message(update: Update, context):
    uid = update.effective_user.id
    ensure_user(uid, update.effective_chat.id)
    save_message(uid, "user", update.message.text)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    msgs = get_recent_messages(uid, limit=20)
    reply = await call_claude(build_system(uid), msgs)
    save_message(uid, "assistant", reply)
    await update.message.reply_text(reply)
    context.application.create_task(learn_from_conversation(uid))

# ---------------- SCHEDULED JOBS ----------------
async def morning_checkin(context: ContextTypes.DEFAULT_TYPE):
    for uid, chat_id in all_user_ids():
        try:
            msgs = get_recent_messages(uid, limit=6)
            msgs.append({"role": "user", "content": "[Morning. Send Renē a short morning check-in: a quick mindset hit and ONE question to set his day. Reference a goal or recent commitment from memory if relevant. Punchy, not a lecture.]"})
            reply = await call_claude(build_system(uid), msgs)
            save_message(uid, "assistant", reply)
            await context.bot.send_message(chat_id=chat_id, text=reply)
        except Exception as e:
            logger.error(f"Morning checkin failed {uid}: {e}")

async def random_push(context: ContextTypes.DEFAULT_TYPE):
    for uid, chat_id in all_user_ids():
        try:
            mode = random.choice(["wisdom", "progress"])
            if mode == "wisdom":
                instr = "[Send a short message: one piece of wisdom from a great mentor/coach/book, then 1-2 lines on how it applies to Renē's exact situation right now (use memory). Make it land.]"
            else:
                instr = "[Send a short progress reflection: remind Renē how far he's come based on memory — something concrete he did or changed. Anchor it so he feels momentum, not flattery.]"
            msgs = get_recent_messages(uid, limit=6)
            msgs.append({"role": "user", "content": instr})
            reply = await call_claude(build_system(uid), msgs)
            save_message(uid, "assistant", reply)
            await context.bot.send_message(chat_id=chat_id, text=reply)
        except Exception as e:
            logger.error(f"Random push failed {uid}: {e}")

async def send_weekly_review(bot, uid, chat_id):
    instr = "[It's the weekly review. Using memory and recent conversations, give Renē: 1) his wins this week, 2) where he slipped or made excuses, 3) the single most important focus for next week, 4) any pattern you're noticing he should watch. Be honest and specific, sharp older-brother tone. Use questions where it makes him think.]"
    msgs = get_recent_messages(uid, limit=14)
    msgs.append({"role": "user", "content": instr})
    reply = await call_claude(build_system(uid), msgs, max_tokens=1200)
    save_message(uid, "assistant", reply)
    await bot.send_message(chat_id=chat_id, text=reply)

async def weekly_review_job(context: ContextTypes.DEFAULT_TYPE):
    for uid, chat_id in all_user_ids():
        try:
            await send_weekly_review(context.bot, uid, chat_id)
        except Exception as e:
            logger.error(f"Weekly review failed {uid}: {e}")

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    for name, fn in [("start", start), ("checkin", checkin), ("remember", remember),
                     ("memories", memories), ("forget", forget), ("profile", profile_cmd),
                     ("review", review), ("reset", reset)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    jq = app.job_queue
    jq.run_daily(morning_checkin, time=local_time_to_utc(9, 30))      # ~9:30 Latvia
    jq.run_daily(random_push, time=local_time_to_utc(15, 0))          # ~15:00 daytime push
    jq.run_daily(weekly_review_job, time=local_time_to_utc(19, 0), days=(6,))  # Sunday 19:00

    logger.info("Upgraded mentor bot running (memory + patterns + scheduled pushes)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
