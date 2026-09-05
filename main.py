import os
import sqlite3
import logging
import asyncio
from dotenv import load_dotenv
from aiohttp import web
from openai import AsyncOpenAI

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Настройки каналов
TG_CHANNEL_USERNAME = "beatsbyblaes"
YT_CHANNEL_URL = "https://www.youtube.com/@prodblaes/videos"
ADMIN_USERNAME = "beatsbyblaes" # Юзернейм админа для покупки подписки

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# --- БАЗА ДАННЫХ (SQLite) ---
def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            lang TEXT DEFAULT 'RU',
            seo_used INTEGER DEFAULT 0,
            parser_used INTEGER DEFAULT 0,
            is_subscribed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT lang, seo_used, parser_used, is_subscribed FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (user_id, lang, seo_used, parser_used, is_subscribed) VALUES (?, 'RU', 0, 0, 0)", (user_id,))
        conn.commit()
        row = ('RU', 0, 0, 0)
    conn.close()
    return {"lang": row[0], "seo_used": row[1], "parser_used": row[2], "is_subscribed": row[3]}

def update_user_lang(user_id, lang):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))
    conn.commit()
    conn.close()

def increment_limit(user_id, limit_type):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    if limit_type == "seo":
        cursor.execute("UPDATE users SET seo_used = seo_used + 1 WHERE user_id = ?", (user_id,))
    elif limit_type == "parser":
        cursor.execute("UPDATE users SET parser_used = parser_used + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

init_db()

# --- СЛОВАРЬ ЛОКАЛИЗАЦИИ ---
TEXTS = {
    "RU": {
        "welcome": "👋 Привет! Я AI-помощник для битмейкеров.\n\nВыбери нужную функцию:",
        "gen_seo": "🚀 Сгенерировать SEO",
        "parse_competitor": "🔍 Разобрать конкурента",
        "change_lang": "🌐 Язык / Language",
        "select_lang": "Выберите язык интерфейса:",
        "lang_changed": "✅ Язык успешно изменен на Русский!",
        "ask_seo_topic": "✍️ Напиши название и жанр бита (например: 'Drake type beat, Drake, Travis Scott'):",
        "ask_competitor_url": "🔗 Отправь ссылку на YouTube-видео конкурента:",
        "sub_required": f"⚠️ **Для использования бота нужно подписаться на наш Telegram и YouTube!**\n\n1. Подпишись на [Telegram-канал](https://t.me/{TG_CHANNEL_USERNAME})\n2. Подпишись на [YouTube-канал]({YT_CHANNEL_URL})\n3. Нажми кнопку «Проверить подписку» ниже.",
        "check_sub_btn": "✅ Проверить подписку",
        "limit_reached": "🔒 **Бесплатный лимит исчерпан!**\n\nВы уже использовали 1 бесплатную генерацию/разбор.\nДля продолжения работы оформите подписку.",
        "buy_sub_btn": "⭐ Оформить подписку",
        "generating": "🤖 Генерирую SEO...",
        "parsing": "🔍 Парсим теги с YouTube...",
        "buy_info": f"💳 Для покупки безлимитной подписки напишите администратору: @{ADMIN_USERNAME}"
    },
    "EN": {
        "welcome": "👋 Hi! I am an AI assistant for beatmakers.\n\nChoose an option:",
        "gen_seo": "🚀 Generate SEO",
        "parse_competitor": "🔍 Analyze Competitor",
        "change_lang": "🌐 Language / Язык",
        "select_lang": "Select interface language:",
        "lang_changed": "✅ Language successfully changed to English!",
        "ask_seo_topic": "✍️ Enter the title and genre of the beat (e.g., 'Drake type beat, Drake, Travis Scott'):",
        "ask_competitor_url": "🔗 Send a link to the competitor's YouTube video:",
        "sub_required": f"⚠️ **To use the bot, please subscribe to our Telegram and YouTube!**\n\n1. Join our [Telegram Channel](https://t.me/{TG_CHANNEL_USERNAME})\n2. Subscribe to our [YouTube Channel]({YT_CHANNEL_URL})\n3. Click 'Check Subscription' below.",
        "check_sub_btn": "✅ Check Subscription",
        "limit_reached": "🔒 **Free limit reached!**\n\nYou have used your 1 free generation/analysis.\nPlease subscribe to get unlimited access.",
        "buy_sub_btn": "⭐ Subscribe Now",
        "generating": "🤖 Generating SEO...",
        "parsing": "🔍 Parsing tags from YouTube...",
        "buy_info": f"💳 To purchase an unlimited subscription, contact the admin: @{ADMIN_USERNAME}"
    }
}

class BotStates(StatesGroup):
    waiting_for_seo_input = State()
    waiting_for_competitor_url = State()

def get_main_keyboard(lang):
    kb = [
        [types.KeyboardButton(text=TEXTS[lang]["gen_seo"])],
        [types.KeyboardButton(text=TEXTS[lang]["parse_competitor"])],
        [types.KeyboardButton(text=TEXTS[lang]["change_lang"])]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ПРОВЕРКА ПОДПИСКИ НА ТГ ---
async def is_subscribed_to_tg(user_id):
    try:
        member = await bot.get_chat_member(chat_id=f"@{TG_CHANNEL_USERNAME}", user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# --- ХЭНДЛЕРЫ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    u = get_user(message.from_user.id)
    await message.answer(TEXTS[u["lang"]]["welcome"], reply_markup=get_main_keyboard(u["lang"]))

@dp.message(F.text.in_([TEXTS["RU"]["change_lang"], TEXTS["EN"]["change_lang"]]))
async def lang_menu(message: types.Message):
    u = get_user(message.from_user.id)
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang_RU"),
         InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang_EN")]
    ])
    await message.answer(TEXTS[u["lang"]]["select_lang"], reply_markup=inline_kb)

@dp.callback_query(F.data.startswith("set_lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[2]
    update_user_lang(callback.from_user.id, lang)
    await callback.message.delete()
    await callback.message.answer(TEXTS[lang]["lang_changed"], reply_markup=get_main_keyboard(lang))

@dp.message(F.text.in_([TEXTS["RU"]["gen_seo"], TEXTS["EN"]["gen_seo"]]))
async def start_seo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    u = get_user(user_id)
    lang = u["lang"]

    if not await is_subscribed_to_tg(user_id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Telegram Channel", url=f"https://t.me/{TG_CHANNEL_USERNAME}")],
            [InlineKeyboardButton(text="▶️ YouTube Channel", url=YT_CHANNEL_URL)],
            [InlineKeyboardButton(text=TEXTS[lang]["check_sub_btn"], callback_data="check_sub")]
        ])
        await message.answer(TEXTS[lang]["sub_required"], reply_markup=kb, parse_mode="Markdown")
        return

    if u["seo_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        return

    await state.set_state(BotStates.waiting_for_seo_input)
    await message.answer(TEXTS[lang]["ask_seo_topic"])

@dp.message(F.text.in_([TEXTS["RU"]["parse_competitor"], TEXTS["EN"]["parse_competitor"]]))
async def start_parser(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    u = get_user(user_id)
    lang = u["lang"]

    if not await is_subscribed_to_tg(user_id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Telegram Channel", url=f"https://t.me/{TG_CHANNEL_USERNAME}")],
            [InlineKeyboardButton(text="▶️ YouTube Channel", url=YT_CHANNEL_URL)],
            [InlineKeyboardButton(text=TEXTS[lang]["check_sub_btn"], callback_data="check_sub")]
        ])
        await message.answer(TEXTS[lang]["sub_required"], reply_markup=kb, parse_mode="Markdown")
        return

    if u["parser_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        return

    await state.set_state(BotStates.waiting_for_competitor_url)
    await message.answer(TEXTS[lang]["ask_competitor_url"])

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    u = get_user(user_id)
    if await is_subscribed_to_tg(user_id):
        await callback.message.delete()
        await callback.message.answer("🎉 Спасибо за подписку! Доступ открыт.", reply_markup=get_main_keyboard(u["lang"]))
    else:
        await callback.answer("❌ Подписка не найдена. Подпишитесь на канал!", show_alert=True)

@dp.callback_query(F.data == "buy_subscription")
async def buy_sub_callback(callback: types.CallbackQuery):
    u = get_user(callback.from_user.id)
    await callback.message.answer(TEXTS[u["lang"]]["buy_info"])
    await callback.answer()

# --- ИСПОЛНЕНИЕ SEO ---
@dp.message(BotStates.waiting_for_seo_input)
async def process_seo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    u = get_user(user_id)
    lang = u["lang"]

    # Двойная проверка лимита перед запуском запроса к AI
    if u["seo_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        await state.clear()
        return

    await message.answer(TEXTS[lang]["generating"])

    system_instruction = (
        "Ты — генератор SEO для YouTube битмейкеров.\n"
        "Твоя задача — сгенерировать SEO-текст СТРОГО на английском языке по указанному ниже шаблону.\n\n"
        "ПРАВИЛА И ФОРМАТ:\n"
        "1. Придумай ОДНО название бита (Beat Name). Оно должно быть СТРОГО ОДИНАКОВЫМ в Title и в первой строке Description.\n"
        "2. Запрещено писать любые приветствия, вводные или заключительные слова, советы и комментарии.\n"
        "3. Описание ДОЛЖНО СТРОГО следовать этой структуре:\n\n"
        "Title: [FREE] [Artist] Type Beat 2026 - '[Beat Name]'\n\n"
        "Description:\n"
        "[FREE] [Artist] Type Beat 2026 - '[Beat Name]'\n\n"
        "INSTAGRAM: [your instagram]\n\n"
        "MAIL: [your mail]\n\n"
        "BPM: [BPM]\n"
        "KEY: [Key]\n\n"
        "FREE FOR NON PROFIT USE, FOR COMMERCIAL USE PLEASE PURCHASE A LEASE. ANYONE WHO RELEASES A SONG WITHOUT A LEASE WILL BE HIT WITH COPYRIGHT.\n\n"
        "FREE ONLY FOR SOUNDCLOUD (prod. [your name])\n\n"
        "Dont forget to like & subscribe,\n\n"
        "[Подберите 15-20 релевантных SEO тегов через запятую в стиле битмейкинга]\n\n"
        "Tags:\n"
        "[Те же теги списком через запятую]\n\n"
        "4. Если пользователь не указал BPM или Key, придумай подходящие значения под этот жанр."
    )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="openrouter/auto",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Сгенерируй SEO для бита: {message.text}"}
                ]
            ),
            timeout=30.0
        )
        
        increment_limit(user_id, "seo")
        await state.clear()
        await message.answer(response.choices[0].message.content)

    except asyncio.TimeoutError:
        await state.clear()
        await message.answer("⚠️ Сервер нейросети перегружен и не ответил за 30 секунд. Попробуй ещё раз чуть позже!")
    except Exception as e:
        await state.clear()
        await message.answer(f"⚠️ Ошибка при запросе к AI: {str(e)}")

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Bot is active!")

async def main():
    logging.basicConfig(level=logging.INFO)
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print("🚀 Bot with Freemium system launched!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())