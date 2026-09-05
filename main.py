import os
import sqlite3
import logging
import asyncio
import aiohttp
from dotenv import load_dotenv
from aiohttp import web
from openai import AsyncOpenAI

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery

# Загрузка переменных окружения из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN")
PROVIDER_TOKEN_YUKASSA = os.getenv("PROVIDER_TOKEN_YUKASSA") # Токен ЮKassa из BotFather

TG_CHANNEL_USERNAME = "beatsbyblaes"
YT_CHANNEL_URL = "https://www.youtube.com/@prodblaes/videos"
SUB_PRICE_RUB = 490 # Цена подписки в рублях
SUB_PRICE_USD = 5   # Цена подписки в USD для CryptoBot

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# --- БАЗА ДАННЫХ ---
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

def set_premium(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_subscribed = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

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
        "sub_required": f"⚠️ **Для использования бота нужно подписаться на наш Telegram и YouTube!**\n\n1. Подпишись на [Telegram-канал](https://t.me/{TG_CHANNEL_USERNAME})\n2. Подпишись на [YouTube-канал]({YT_CHANNEL_URL})\n3. Нажми кнопку «Проверить подписку» ниже.",
        "check_sub_btn": "✅ Проверить подписку",
        "limit_reached": "🔒 **Бесплатный лимит исчерпан!**\n\nВы уже использовали 1 бесплатную генерацию.\nОформите подписку для безлимитного доступа.",
        "buy_sub_btn": "⭐ Оформить подписку",
        "generating": "🤖 Генерирую SEO...",
        "select_pay_method": "💳 Выберите удобный способ оплаты подписки:",
        "pay_success": "🎉 Поздравляем! Безлимитный доступ успешно активирован."
    },
    "EN": {
        "welcome": "👋 Hi! I am an AI assistant for beatmakers.\n\nChoose an option:",
        "gen_seo": "🚀 Generate SEO",
        "parse_competitor": "🔍 Analyze Competitor",
        "change_lang": "🌐 Language / Язык",
        "select_lang": "Select interface language:",
        "lang_changed": "✅ Language successfully changed to English!",
        "ask_seo_topic": "✍️ Enter the title and genre of the beat (e.g., 'Drake type beat, Drake, Travis Scott'):",
        "sub_required": f"⚠️ **To use the bot, please subscribe to our Telegram and YouTube!**\n\n1. Join our [Telegram Channel](https://t.me/{TG_CHANNEL_USERNAME})\n2. Subscribe to our [YouTube Channel]({YT_CHANNEL_URL})\n3. Click 'Check Subscription' below.",
        "check_sub_btn": "✅ Check Subscription",
        "limit_reached": "🔒 **Free limit reached!**\n\nYou have used your free limit.\nGet an unlimited subscription to continue.",
        "buy_sub_btn": "⭐ Subscribe Now",
        "generating": "🤖 Generating SEO...",
        "select_pay_method": "💳 Choose a payment method for unlimited access:",
        "pay_success": "🎉 Congratulations! Unlimited access is now active."
    }
}

class BotStates(StatesGroup):
    waiting_for_seo_input = State()

def get_main_keyboard(lang):
    kb = [
        [types.KeyboardButton(text=TEXTS[lang]["gen_seo"])],
        [types.KeyboardButton(text=TEXTS[lang]["change_lang"])]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ПРОВЕРКА ПОДПИСКИ НА ТГ КАНАЛ ---
async def is_subscribed_to_tg(user_id):
    try:
        member = await bot.get_chat_member(chat_id=f"@{TG_CHANNEL_USERNAME}", user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# --- API CRYPTOBOT ---
async def create_crypto_invoice(user_id, amount_usd):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    payload = {
        "asset": "USDT",
        "amount": str(amount_usd),
        "description": "Unlimited Beatmaker AI Access",
        "payload": str(user_id)
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("ok"):
                return data["result"]["bot_invoice_url"], data["result"]["invoice_id"]
            return None, None

async def check_crypto_invoice(invoice_id):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    payload = {"invoice_ids": [invoice_id]}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if data.get("ok") and len(data["result"]["items"]) > 0:
                return data["result"]["items"][0]["status"] == "paid"
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

# --- ВЫБОР И ОФОРМЛЕНИЕ ОПЛАТЫ ---
@dp.callback_query(F.data == "buy_subscription")
async def show_payment_options(callback: types.CallbackQuery):
    u = get_user(callback.from_user.id)
    lang = u["lang"]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 СБП / Карта (ЮKassa)", callback_data="pay_yukassa")],
        [InlineKeyboardButton(text="💎 Криптовалюта (CryptoBot)", callback_data="pay_crypto")]
    ])
    await callback.message.answer(TEXTS[lang]["select_pay_method"], reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "pay_yukassa")
async def pay_yukassa_handler(callback: types.CallbackQuery):
    if not PROVIDER_TOKEN_YUKASSA:
        await callback.answer("⚠️ Токен ЮKassa еще не подключен в BotFather.", show_alert=True)
        return

    prices = [LabeledPrice(label="Безлимитная подписка", amount=SUB_PRICE_RUB * 100)]
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Безлимитная подписка",
        description="Пожизненный доступ к генерации SEO.",
        provider_token=PROVIDER_TOKEN_YUKASSA,
        currency="RUB",
        prices=prices,
        start_parameter="unlimited_sub",
        payload=f"sub_{callback.from_user.id}"
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    set_premium(message.from_user.id)
    u = get_user(message.from_user.id)
    await message.answer(TEXTS[u["lang"]]["pay_success"])

@dp.callback_query(F.data == "pay_crypto")
async def pay_crypto_handler(callback: types.CallbackQuery):
    invoice_url, invoice_id = await create_crypto_invoice(callback.from_user.id, SUB_PRICE_USD)
    if invoice_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Оплатить через CryptoBot", url=invoice_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_pay_{invoice_id}")]
        ])
        await callback.message.answer("Оплатите счет через CryptoBot и нажмите «Проверить оплату»:", reply_markup=kb)
    else:
        await callback.answer("⚠️ Ошибка создания счета. Проверьте настройки CryptoBot.", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("check_pay_"))
async def check_crypto_pay_handler(callback: types.CallbackQuery):
    invoice_id = callback.data.split("_")[2]
    is_paid = await check_crypto_invoice(invoice_id)
    
    if is_paid:
        set_premium(callback.from_user.id)
        u = get_user(callback.from_user.id)
        await callback.message.answer(TEXTS[u["lang"]]["pay_success"])
        await callback.message.delete()
    else:
        await callback.answer("❌ Оплата еще не прошла. Выполните платеж и повторите попытку.", show_alert=True)

# --- ГЕНЕРАЦИЯ SEO ---
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

    if u["is_subscribed"] == 0 and u["seo_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        return

    await state.set_state(BotStates.waiting_for_seo_input)
    await message.answer(TEXTS[lang]["ask_seo_topic"])

@dp.message(BotStates.waiting_for_seo_input)
async def process_seo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    u = get_user(user_id)
    lang = u["lang"]

    if u["is_subscribed"] == 0 and u["seo_used"] >= 1:
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
        await message.answer("⚠️ Сервер нейросети перегружен. Попробуй ещё раз чуть позже!")
    except Exception as e:
        await state.clear()
        await message.answer(f"⚠️ Ошибка: {str(e)}")

# --- ВЕБ-СЕРВЕР DUMMY DOCKER ---
async def handle(request):
    return web.Response(text="Bot is running!")

async def main():
    logging.basicConfig(level=logging.INFO)
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print("🚀 Bot launcher active!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())