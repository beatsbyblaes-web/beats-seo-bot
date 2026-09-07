import os
import uuid
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
import aiohttp
from dotenv import load_dotenv
from aiohttp import web
from openai import AsyncOpenAI

import psycopg2
from psycopg2.extras import RealDictCursor

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN")

# База данных PostgreSQL (если указан DATABASE_URL) или локальный SQLite
DATABASE_URL = os.getenv("DATABASE_URL")

# Параметры официального API ЮKassa
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "1455462")
YOOKASSA_API_KEY = os.getenv("YOOKASSA_API_KEY")

# ID администраторов
ADMIN_IDS = [7742046461]

TG_CHANNEL_USERNAME = "beatsbyblaes"
YT_CHANNEL_URL = "https://www.youtube.com/@prodblaes/videos"

PLANS = {
    "1m": {"name": "1 месяц", "days": 30, "price_rub": 390, "price_usd": 4},
    "3m": {"name": "3 месяца", "days": 90, "price_rub": 890, "price_usd": 9},
    "1y": {"name": "1 год", "days": 365, "price_rub": 2490, "price_usd": 25}
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Подключение Gemini через OpenRouter
client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)
GEMINI_MODEL = "google/gemini-2.5-flash"

# --- БАЗА ДАННЫХ (POSTGRESQL / SQLITE) ---
def get_db_connection():
    if DATABASE_URL:
        # Для PostgreSQL на Render
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(url)
    else:
        # Локально на компьютере
        return sqlite3.connect("users.db")

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            lang TEXT DEFAULT 'RU',
            seo_used INTEGER DEFAULT 0,
            parser_used INTEGER DEFAULT 0,
            sub_until TEXT DEFAULT NULL
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()

def get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    param_char = "%s" if DATABASE_URL else "?"
    cursor.execute(f"SELECT lang, seo_used, parser_used, sub_until FROM users WHERE user_id = {param_char}", (user_id,))
    row = cursor.fetchone()
    
    if not row:
        cursor.execute(
            f"INSERT INTO users (user_id, lang, seo_used, parser_used, sub_until) VALUES ({param_char}, 'RU', 0, 0, NULL)",
            (user_id,)
        )
        conn.commit()
        row = ('RU', 0, 0, None)
        
    cursor.close()
    conn.close()
    return {"lang": row[0], "seo_used": row[1], "parser_used": row[2], "sub_until": row[3]}

def reset_user_limits(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    param_char = "%s" if DATABASE_URL else "?"
    cursor.execute(f"UPDATE users SET seo_used = 0, parser_used = 0, sub_until = NULL WHERE user_id = {param_char}", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

def is_user_subscribed(user_id):
    if user_id in ADMIN_IDS:
        return True
    u = get_user(user_id)
    if not u["sub_until"]:
        return False
    try:
        expire_date = datetime.strptime(u["sub_until"], "%Y-%m-%d %H:%M:%S")
        return expire_date > datetime.now()
    except Exception:
        return False

def add_subscription_days(user_id, days):
    conn = get_db_connection()
    cursor = conn.cursor()
    param_char = "%s" if DATABASE_URL else "?"
    u = get_user(user_id)
    now = datetime.now()
    
    if u["sub_until"]:
        try:
            current_expire = datetime.strptime(u["sub_until"], "%Y-%m-%d %H:%M:%S")
            new_expire = max(current_expire, now) + timedelta(days=days)
        except Exception:
            new_expire = now + timedelta(days=days)
    else:
        new_expire = now + timedelta(days=days)

    new_expire_str = new_expire.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(f"UPDATE users SET sub_until = {param_char} WHERE user_id = {param_char}", (new_expire_str, user_id))
    conn.commit()
    cursor.close()
    conn.close()
    return new_expire_str

def update_user_lang(user_id, lang):
    conn = get_db_connection()
    cursor = conn.cursor()
    param_char = "%s" if DATABASE_URL else "?"
    cursor.execute(f"UPDATE users SET lang = {param_char} WHERE user_id = {param_char}", (lang, user_id))
    conn.commit()
    cursor.close()
    conn.close()

def increment_limit(user_id, limit_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    param_char = "%s" if DATABASE_URL else "?"
    if limit_type == "seo":
        cursor.execute(f"UPDATE users SET seo_used = seo_used + 1 WHERE user_id = {param_char}", (user_id,))
    elif limit_type == "parser":
        cursor.execute(f"UPDATE users SET parser_used = parser_used + 1 WHERE user_id = {param_char}", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

init_db()

# --- ТЕКСТЫ ---
TEXTS = {
    "RU": {
        "welcome": "👋 Привет! Я AI-помощник для битмейкеров на базе Google Gemini.\n\nВыбери нужную функцию:",
        "gen_seo": "🚀 Сгенерировать SEO",
        "parse_competitor": "🔍 Разобрать конкурента",
        "change_lang": "🌐 Язык / Language",
        "select_lang": "Выберите язык интерфейса:",
        "lang_changed": "✅ Язык успешно изменен на Русский!",
        "ask_seo_topic": "✍️ Напиши название и жанр бита (например: 'Drake type beat, Drake, Travis Scott'):",
        "ask_competitor_url": "🔗 Отправь ссылку или название трека конкурента для разбора:",
        "sub_required": f"⚠️ **Для использования бота нужно подписаться на наш Telegram и YouTube!**\n\n1. Подпишись на [Telegram-канал](https://t.me/{TG_CHANNEL_USERNAME})\n2. Подпишись на [YouTube-канал]({YT_CHANNEL_URL})\n3. Нажми кнопку «Проверить подписку» ниже.",
        "check_sub_btn": "✅ Проверить подписку",
        "sub_success_alert": "🎉 Спасибо за подписку! Доступ открыт.",
        "sub_fail_alert": "❌ Подписка на Telegram-канал не найдена. Подпишитесь и попробуйте снова!",
        "limit_reached": "🔒 **Бесплатный лимит исчерпан!**\n\nВы уже использовали бесплатную попытку.\nОформите подписку для продолжения работы.",
        "buy_sub_btn": "⭐ Оформить подписку",
        "generating": "🤖 Gemini генерирует ответ...",
        "choose_plan": "🔥 **Выберите тарифный план:**\n\nПолучите неограниченный доступ к генерации SEO описаний и разбору треков.",
        "choose_method": "💳 **Тариф:** {plan_name}\n**Сумма к оплате:** {price_rub} ₽ / ${price_usd}\n\nВыберите способ оплаты:",
        "pay_success": "🎉 **Оплата прошла успешно!**\nПодписка активна до: {until}",
        "cancel_btn": "❌ Отмена",
        "canceled": "Действие отменено."
    },
    "EN": {
        "welcome": "👋 Hi! I am a Gemini-powered AI assistant for beatmakers.\n\nChoose an option:",
        "gen_seo": "🚀 Generate SEO",
        "parse_competitor": "🔍 Analyze Competitor",
        "change_lang": "🌐 Language / Язык",
        "select_lang": "Select interface language:",
        "lang_changed": "✅ Language successfully changed to English!",
        "ask_seo_topic": "✍️ Enter the title and genre of the beat (e.g., 'Drake type beat, Drake, Travis Scott'):",
        "ask_competitor_url": "🔗 Send competitor video title or link for analysis:",
        "sub_required": f"⚠️ **To use the bot, please subscribe to our Telegram and YouTube!**\n\n1. Join our [Telegram Channel](https://t.me/{TG_CHANNEL_USERNAME})\n2. Subscribe to our [YouTube Channel]({YT_CHANNEL_URL})\n3. Click 'Check Subscription' below.",
        "check_sub_btn": "✅ Check Subscription",
        "sub_success_alert": "🎉 Subscription verified!",
        "sub_fail_alert": "❌ Channel subscription not found. Please subscribe first!",
        "limit_reached": "🔒 **Free limit reached!**\n\nYou have used your free generation limit.\nGet a subscription to continue.",
        "buy_sub_btn": "⭐ Subscribe Now",
        "generating": "🤖 Gemini is generating output...",
        "choose_plan": "🔥 **Choose your subscription plan:**\n\nGet unlimited access to AI YouTube SEO optimization for your beats.",
        "choose_method": "💳 **Plan:** {plan_name}\n**Price:** {price_rub} RUB / ${price_usd}\n\nSelect a payment method:",
        "pay_success": "🎉 **Payment successful!**\nSubscription active until: {until}",
        "cancel_btn": "❌ Cancel",
        "canceled": "Action canceled."
    }
}

MENU_BUTTONS = [
    TEXTS["RU"]["gen_seo"], TEXTS["EN"]["gen_seo"],
    TEXTS["RU"]["parse_competitor"], TEXTS["EN"]["parse_competitor"],
    TEXTS["RU"]["change_lang"], TEXTS["EN"]["change_lang"]
]

class BotStates(StatesGroup):
    waiting_for_seo_input = State()
    waiting_for_competitor_input = State()

def get_main_keyboard(lang):
    kb = [
        [types.KeyboardButton(text=TEXTS[lang]["gen_seo"])],
        [types.KeyboardButton(text=TEXTS[lang]["parse_competitor"])],
        [types.KeyboardButton(text=TEXTS[lang]["change_lang"])]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard(lang):
    kb = [
        [types.KeyboardButton(text=TEXTS[lang]["cancel_btn"])]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

async def is_subscribed_to_tg(user_id):
    if user_id in ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id=f"@{TG_CHANNEL_USERNAME}", user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Error checking sub: {e}")
        return False

# --- ХЭНДЛЕРЫ МЕНЮ И ОТМЕНЫ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    u = get_user(message.from_user.id)
    await message.answer(TEXTS[u["lang"]]["welcome"], reply_markup=get_main_keyboard(u["lang"]))

@dp.message(Command("reset"))
async def reset_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    reset_user_limits(message.from_user.id)
    await message.answer("🔄 Все данные и лимиты текущего аккаунта сброшены до 0.")

@dp.message(F.text.in_([TEXTS["RU"]["cancel_btn"], TEXTS["EN"]["cancel_btn"]]))
async def cancel_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    u = get_user(message.from_user.id)
    await message.answer(TEXTS[u["lang"]]["canceled"], reply_markup=get_main_keyboard(u["lang"]))

@dp.message(F.text.in_([TEXTS["RU"]["change_lang"], TEXTS["EN"]["change_lang"]]))
async def lang_menu(message: types.Message, state: FSMContext):
    await state.clear()
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

@dp.callback_query(F.data == "check_sub")
async def check_sub_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    u = get_user(user_id)
    lang = u["lang"]

    if await is_subscribed_to_tg(user_id):
        await callback.answer(TEXTS[lang]["sub_success_alert"], show_alert=True)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(TEXTS[lang]["welcome"], reply_markup=get_main_keyboard(lang))
    else:
        await callback.answer(TEXTS[lang]["sub_fail_alert"], show_alert=True)

# --- ПЛАТЕЖИ: ЮKASSA ---
async def create_yookassa_payment(user_id, plan_key):
    plan = PLANS[plan_key]
    url = "https://api.yookassa.ru/v3/payments"
    auth = aiohttp.BasicAuth(str(YOOKASSA_SHOP_ID), str(YOOKASSA_API_KEY))
    idempotence_key = str(uuid.uuid4())
    headers = {"Idempotence-Key": idempotence_key, "Content-Type": "application/json"}

    bot_user = await bot.get_me()
    payload = {
        "amount": {
            "value": f"{plan['price_rub']}.00",
            "currency": "RUB"
        },
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{bot_user.username}"
        },
        "capture": True,
        "description": f"Подписка {plan['name']} - BeatsBoost",
        "metadata": {
            "user_id": str(user_id),
            "plan_key": plan_key
        }
    }

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status == 200:
                payment_url = data["confirmation"]["confirmation_url"]
                payment_id = data["id"]
                return payment_url, payment_id
            else:
                logging.error(f"YooKassa error: {data}")
                return None, None

async def check_yookassa_payment(payment_id):
    url = f"https://api.yookassa.ru/v3/payments/{payment_id}"
    auth = aiohttp.BasicAuth(str(YOOKASSA_SHOP_ID), str(YOOKASSA_API_KEY))

    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                is_paid = data["status"] == "succeeded"
                return is_paid, data.get("metadata", {})
            return False, {}

# --- ПЛАТЕЖИ: CRYPTOBOT ---
async def create_crypto_invoice(user_id, plan_key):
    plan = PLANS[plan_key]
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    payload = {
        "asset": "USDT",
        "amount": str(plan["price_usd"]),
        "description": f"Subscription {plan['name']} - Beatmaker SEO Bot",
        "payload": f"{user_id}:{plan_key}"
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
                item = data["result"]["items"][0]
                return item["status"] == "paid", item.get("payload")
            return False, None

# --- МЕНЮ ТАРИФОВ И ОПЛАТЫ ---
@dp.callback_query(F.data == "buy_subscription")
async def show_plans_menu(callback: types.CallbackQuery):
    u = get_user(callback.from_user.id)
    lang = u["lang"]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 1 месяц — 390 ₽ / $4", callback_data="plan_1m")],
        [InlineKeyboardButton(text="🔥 3 месяца — 890 ₽ / $9", callback_data="plan_3m")],
        [InlineKeyboardButton(text="💎 1 год — 2 490 ₽ / $25", callback_data="plan_1y")]
    ])
    await callback.message.answer(TEXTS[lang]["choose_plan"], reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("plan_"))
async def show_methods_menu(callback: types.CallbackQuery):
    plan_key = callback.data.split("_")[1]
    plan = PLANS[plan_key]
    u = get_user(callback.from_user.id)
    lang = u["lang"]

    text = TEXTS[lang]["choose_method"].format(
        plan_name=plan["name"],
        price_rub=plan["price_rub"],
        price_usd=plan["price_usd"]
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 СБП / Карты / T-Pay (ЮKassa)", callback_data=f"pay_yk_{plan_key}")],
        [InlineKeyboardButton(text="💎 CryptoBot (USDT / TON)", callback_data=f"pay_crypto_{plan_key}")],
        [InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="buy_subscription")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("pay_yk_"))
async def pay_yookassa_handler(callback: types.CallbackQuery):
    plan_key = callback.data.split("_")[2]
    plan = PLANS[plan_key]

    if not YOOKASSA_API_KEY:
        await callback.answer("⚠️ ЮKassa API ключ ещё не добавлен на сервере!", show_alert=True)
        return

    payment_url, payment_id = await create_yookassa_payment(callback.from_user.id, plan_key)

    if payment_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Перейти к оплате (СБП / Карта)", url=payment_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"chk_yk_{payment_id}")]
        ])
        await callback.message.answer(
            f"Оплата тарифа **{plan['name']}** на сумму **{plan['price_rub']} ₽**.\n\n"
            "Нажмите кнопку ниже, выберите СБП или свой банк и подтвердите платёж. "
            "После возвращения нажмите кнопку «Проверить оплату».",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    else:
        await callback.answer("⚠️ Ошибка создания платежа в ЮKassa.", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("chk_yk_"))
async def check_yk_callback(callback: types.CallbackQuery):
    payment_id = callback.data.split("_")[2]
    is_paid, metadata = await check_yookassa_payment(payment_id)

    if is_paid and metadata:
        user_id = int(metadata["user_id"])
        plan_key = metadata["plan_key"]
        plan = PLANS[plan_key]
        until_str = add_subscription_days(user_id, plan["days"])
        u = get_user(user_id)
        await callback.message.answer(TEXTS[u["lang"]]["pay_success"].format(until=until_str), parse_mode="Markdown")
        await callback.message.delete()
    else:
        await callback.answer("❌ Платёж ещё не подтверждён банком. Попробуйте через пару секунд!", show_alert=True)

@dp.callback_query(F.data.startswith("pay_crypto_"))
async def pay_crypto_handler(callback: types.CallbackQuery):
    plan_key = callback.data.split("_")[2]
    invoice_url, invoice_id = await create_crypto_invoice(callback.from_user.id, plan_key)
    
    if invoice_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Перейти к оплате в CryptoBot", url=invoice_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"chk_crypto_{invoice_id}")]
        ])
        await callback.message.answer("Оплатите счет в CryptoBot и нажмите кнопку проверки:", reply_markup=kb)
    else:
        await callback.answer("⚠️ Не удалось создать счет CryptoBot.", show_alert=True)
    await callback.answer()

@dp.callback_query(F.data.startswith("chk_crypto_"))
async def check_crypto_callback(callback: types.CallbackQuery):
    invoice_id = callback.data.split("_")[2]
    is_paid, payload = await check_crypto_invoice(invoice_id)

    if is_paid and payload:
        user_id_str, plan_key = payload.split(":")
        plan = PLANS[plan_key]
        until_str = add_subscription_days(int(user_id_str), plan["days"])
        u = get_user(int(user_id_str))
        await callback.message.answer(TEXTS[u["lang"]]["pay_success"].format(until=until_str), parse_mode="Markdown")
        await callback.message.delete()
    else:
        await callback.answer("❌ Платеж пока не поступил. Попробуйте через пару секунд!", show_alert=True)

# --- ГЕНЕРАЦИЯ SEO ---
@dp.message(F.text.in_([TEXTS["RU"]["gen_seo"], TEXTS["EN"]["gen_seo"]]))
async def start_seo(message: types.Message, state: FSMContext):
    await state.clear()
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

    if not is_user_subscribed(user_id) and u["seo_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        return

    await state.set_state(BotStates.waiting_for_seo_input)
    await message.answer(TEXTS[lang]["ask_seo_topic"], reply_markup=get_cancel_keyboard(lang))

@dp.message(BotStates.waiting_for_seo_input, ~F.text.in_(MENU_BUTTONS + [TEXTS["RU"]["cancel_btn"], TEXTS["EN"]["cancel_btn"]]))
async def process_seo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    u = get_user(user_id)
    lang = u["lang"]

    if not is_user_subscribed(user_id) and u["seo_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        await state.clear()
        return

    await message.answer(TEXTS[lang]["generating"])

    system_instruction = (
        "You are an expert YouTube SEO generator specialized for beatmakers.\n"
        "Generate SEO text STRICTLY in English following the exact structure below.\n\n"
        "RULES:\n"
        "1. Create ONE consistent beat name. It must be identical in Title and the first line of Description.\n"
        "2. Do NOT output any intro, greetings, commentary, or advice.\n"
        "3. Follow this EXACT format:\n\n"
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
        "[15-20 relevant comma-separated SEO tags for the beat style]\n\n"
        "Tags:\n"
        "[Same tags as a comma-separated list]\n\n"
        "4. If BPM or Key is not specified by the user, invent suitable values for the genre."
    )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": f"Generate SEO for beat: {message.text}"}
                ],
                max_tokens=1000
            ),
            timeout=30.0
        )
        
        increment_limit(user_id, "seo")
        await state.clear()
        await message.answer(response.choices[0].message.content, reply_markup=get_main_keyboard(lang))

    except asyncio.TimeoutError:
        await state.clear()
        await message.answer("⚠️ Сервер нейросети перегружен. Попробуй ещё раз чуть позже!", reply_markup=get_main_keyboard(lang))
    except Exception as e:
        await state.clear()
        await message.answer(f"⚠️ Ошибка генерации: {str(e)}", reply_markup=get_main_keyboard(lang))

# --- РАЗБОР КОНКУРЕНТА ---
@dp.message(F.text.in_([TEXTS["RU"]["parse_competitor"], TEXTS["EN"]["parse_competitor"]]))
async def start_parser(message: types.Message, state: FSMContext):
    await state.clear()
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

    if not is_user_subscribed(user_id) and u["parser_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        return

    await state.set_state(BotStates.waiting_for_competitor_input)
    await message.answer(TEXTS[lang]["ask_competitor_url"], reply_markup=get_cancel_keyboard(lang))

@dp.message(BotStates.waiting_for_competitor_input, ~F.text.in_(MENU_BUTTONS + [TEXTS["RU"]["cancel_btn"], TEXTS["EN"]["cancel_btn"]]))
async def process_parser(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    u = get_user(user_id)
    lang = u["lang"]

    if not is_user_subscribed(user_id) and u["parser_used"] >= 1:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["buy_sub_btn"], callback_data="buy_subscription")]
        ])
        await message.answer(TEXTS[lang]["limit_reached"], reply_markup=kb, parse_mode="Markdown")
        await state.clear()
        return

    await message.answer(TEXTS[lang]["generating"])

    parser_instruction = (
        "You are an expert YouTube competitor analyst for beatmakers.\n"
        "Analyze the competitor video title, link, or topic and provide a structured breakdown:\n"
        "1. Estimated style and sub-genre\n"
        "2. Why this title/SEO works well on YouTube algorithms\n"
        "3. Recommended optimized tags inspired by this competitor style (15-20 tags).\n"
        "Keep it professional and concise in English."
    )

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[
                    {"role": "system", "content": parser_instruction},
                    {"role": "user", "content": f"Analyze this competitor: {message.text}"}
                ],
                max_tokens=1000
            ),
            timeout=30.0
        )
        
        increment_limit(user_id, "parser")
        await state.clear()
        await message.answer(response.choices[0].message.content, reply_markup=get_main_keyboard(lang))

    except asyncio.TimeoutError:
        await state.clear()
        await message.answer("⚠️ Сервер нейросети перегружен. Попробуй ещё раз чуть позже!", reply_markup=get_main_keyboard(lang))
    except Exception as e:
        await state.clear()
        await message.answer(f"⚠️ Ошибка анализа: {str(e)}", reply_markup=get_main_keyboard(lang))

# --- СЕРВЕР RENDER ---
async def handle(request):
    return web.Response(text="Bot is running smoothly!")

async def main():
    logging.basicConfig(level=logging.INFO)
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print("🚀 Bot launched with PostgreSQL / SQLite support!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())