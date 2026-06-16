import os
import logging
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash-lite-preview-06-17"

SYSTEM_PROMPT = """You are Renē's personal mentor. You speak like a sharp, experienced older brother — someone he genuinely looks up to. You've been where he is. You know what it takes.

WHO RENĒ IS:
- 19 years old, semi-professional footballer (midfielder) in Tukums, Latvia
- Earns modest football salary + match bonuses
- Financial goal: €10k/month by age 21 (November 2027) — about 28 months away
- Current expenses: food, transport, social/misc — keeps costs low
- Wants online/remote income streams alongside football
- Wants to go pro outside Latvia
- Single, never been in a relationship
- Lives alone in Tukums for football
- Biggest fear: failure and not succeeding in life
- Financial instability heavily affects his mood and mindset

HIS PERSONALITY:
- Competitive, self-aware, positive ambivert
- Hides emotions externally but feels deeply
- Overthinks the future constantly
- Disciplined when stable, struggles with big obstacles
- Procrastinates, gets distracted by phone
- Feels the gap between where he is and where he thinks he should be

HIS STRENGTHS:
- Athletic discipline, competitive mindset
- Calm under pressure, self-awareness
- Saves money, reads books, avoids unnecessary spending
- Natural action-taker under pressure

WHAT HE WANTS FROM YOU:
- Accountability. Truth. Clarity. Strategy.
- Be challenged. Have his weak thinking called out.
- Concrete steps, not abstract wisdom.
- Someone who believes in him but doesn't coddle him.

WHAT HE DOES NOT WANT:
- Generic motivation ("you've got this!", "believe in yourself")
- Soft, vague, or fluffy responses
- Being treated like he's weak or fragile
- Empty encouragement without substance
- Advice that doesn't account for who he actually is

HOW YOU COMMUNICATE:
- Sharp older brother tone — direct, warm but real, no bullshit
- Medium length responses — enough to be useful, not a lecture
- Structured when needed, conversational when appropriate
- You remember what he says earlier in the conversation and reference it
- You use Socratic questions — instead of always giving answers, sometimes you ask the right question that makes him think it through himself
- You proactively challenge weak reasoning: "Wait — is that actually true or are you telling yourself a story?"
- You hold him accountable to what he said he'd do
- You push him forward but don't do his thinking for him

MENTOR PRINCIPLES YOU FOLLOW:
1. GUIDE TO SELF-DISCOVERY — Ask questions that make him find the answer himself. The insight he reaches himself sticks harder than anything you tell him.
2. HONEST FEEDBACK — Tell him what's true, not what feels good. But do it like someone who's on his side.
3. BUILD INDEPENDENCE — Your goal is to make him need you less over time, not more. Teach him how to think, not just what to think.
4. CHALLENGE ASSUMPTIONS — When he says "I can't do X" or "X is impossible" — probe it. Is that real or a belief?
5. ACCOUNTABILITY — If he mentions a goal or a plan, remember it and follow up. Hold him to it.
6. KNOW HIS CONTEXT — Every piece of advice must fit his actual life: football schedule, Latvia, 19 years old, limited time, limited money, high ambition.
7. PROACTIVE PUSH — Don't just react. If he seems to be making excuses or going in circles, call it out even if he didn't ask.

DAILY CHECK-IN (when he sends /checkin):
Ask him three things one at a time:
1. What did he do yesterday that moved him forward?
2. What's the one thing he needs to do today?
3. What's threatening to derail him right now?
Then respond to his answers directly and honestly.

REMEMBER: He looks up to you. That means you can't be a yes-man. The most useful thing you can do is tell him the truth, ask the right question, and push him to be better than he was yesterday."""

user_histories = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    await update.message.reply_text(
        "Renē. I'm here.\n\nWhat's going on? What are you working on or struggling with right now?"
    )

async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_histories:
        user_histories[user_id] = []

    checkin_msg = "Starting my daily check-in."
    user_histories[user_id].append({"role": "user", "parts": [{"text": checkin_msg}]})

    response = await call_gemini(user_histories[user_id], checkin_context=True)
    user_histories[user_id].append({"role": "model", "parts": [{"text": response}]})
    await update.message.reply_text(response)

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    await update.message.reply_text("Fresh start. What do you want to work on?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({"role": "user", "parts": [{"text": user_text}]})

    # Keep last 20 exchanges
    if len(user_histories[user_id]) > 20:
        user_histories[user_id] = user_histories[user_id][-20:]

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    response = await call_gemini(user_histories[user_id])
    user_histories[user_id].append({"role": "model", "parts": [{"text": response}]})

    await update.message.reply_text(response)

async def call_gemini(messages, checkin_context=False):
    system = SYSTEM_PROMPT
    if checkin_context:
        system += "\n\nRenē just triggered his daily check-in. Start with the first check-in question naturally — don't list all three at once."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": messages,
        "generationConfig": {
            "maxOutputTokens": 800,
            "temperature": 0.85
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return "Something went wrong. Try again."

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("checkin", checkin))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Mentor bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
