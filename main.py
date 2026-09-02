import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Загружаем переменные окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_KEY = os.getenv("OPENAI_API_KEY")

# Инициализируем AI-клиент (DeepSeek через OpenRouter)
ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- СЛОВАРЬ ПЕРЕВОДОВ ---
MESSAGES = {
    "ru": {
        "welcome": (
            "Привет, {name}! 👋\n\n"
            "Я AI-помощник для битмейкеров. Помогу составить SEO или разобрать конкурентов.\n"
            "Выбери нужное действие:"
        ),
        "btn_gen_seo": "🚀 Сгенерировать SEO",
        "btn_parse_yt": "🔍 Разобрать конкурента",
        "btn_lang": "🌐 Язык / Language",
        "btn_cancel": "❌ Отмена",
        "btn_back": "⬅️ Назад",
        "select_lang": "Выберите язык интерфейса / Select interface language:",
        "lang_changed": "✅ Язык успешно изменен на Русский!",
        "cancelled": "❌ Действие отменено. Выбери нужное действие:",
        "seo_step1": "🎤 **Шаг 1/3:** Введи имя артиста или стиль (например: `Drake x Travis Scott`):",
        "seo_step2": "🥁 **Шаг 2/3:** Укажи жанр/вайб (например: `Dark Trap`, `Jersey Club`):",
        "seo_step3": "🎹 **Шаг 3/3:** Укажи BPM и тональность (например: `140 BPM / C# Minor`):",
        "seo_generating": "🤖 Нейросеть генерирует уникальный SEO-пакет...",
        "seo_error": "❌ Ошибка генерации AI! Проверь ключ OpenRouter.\n\n`{error}`",
        "parse_step1": "🔗 **Отправь ссылку на YouTube-видео** конкурента:",
        "parse_invalid_url": "❌ Это не похоже на ссылку YouTube. Попробуй еще раз!",
        "parse_parsing": "🔍 Парсю страницу видео и вытаскиваю скрытые теги...",
        "parse_error": "❌ Не удалось разобрать видео. Ошибка: `{error}`",
        "parse_no_tags": "У этого видео нет тегов (или они скрыты).",
        "parse_result": (
            "🎬 **Название видео:**\n*{title}*\n\n"
            "🔑 **Реальные теги видео (готовы к копированию):**\n`{tags}`\n\n"
            "📊 **Всего тегов:** {count} шт."
        ),
    },
    "en": {
        "welcome": (
            "Hello, {name}! 👋\n\n"
            "I'm an AI assistant for beatmakers. I'll help you build SEO or analyze your competitors.\n"
            "Choose an action:"
        ),
        "btn_gen_seo": "🚀 Generate SEO",
        "btn_parse_yt": "🔍 Analyze Competitor",
        "btn_lang": "🌐 Language / Язык",
        "btn_cancel": "❌ Cancel",
        "btn_back": "⬅️ Back",
        "select_lang": "Select interface language / Выберите язык интерфейса:",
        "lang_changed": "✅ Language successfully changed to English!",
        "cancelled": "❌ Action cancelled. Choose an action:",
        "seo_step1": "🎤 **Step 1/3:** Enter artist name or type style (e.g., `Drake x Travis Scott`):",
        "seo_step2": "🥁 **Step 2/3:** Specify genre/vibe (e.g., `Dark Trap`, `Jersey Club`):",
        "seo_step3": "🎹 **Step 3/3:** Specify BPM & Key (e.g., `140 BPM / C# Minor`):",
        "seo_generating": "🤖 AI is generating a unique SEO package...",
        "seo_error": "❌ AI generation error! Check OpenRouter key.\n\n`{error}`",
        "parse_step1": "🔗 **Send a YouTube video link** of your competitor:",
        "parse_invalid_url": "❌ This doesn't look like a valid YouTube link. Try again!",
        "parse_parsing": "🔍 Parsing video page and extracting hidden tags...",
        "parse_error": "❌ Failed to parse video. Error: `{error}`",
        "parse_no_tags": "This video has no tags (or they are hidden).",
        "parse_result": (
            "🎬 **Video Title:**\n*{title}*\n\n"
            "🔑 **Actual Video Tags (Ready to copy):**\n`{tags}`\n\n"
            "📊 **Total Tags:** {count} pcs."
        ),
    },
}


# --- СОСТОЯНИЯ (FSM) ---
class SEOForm(StatesGroup):
    artist = State()
    genre = State()
    bpm_key = State()


class CompetitorForm(StatesGroup):
    yt_link = State()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def get_user_lang(state: FSMContext) -> str:
    data = await state.get_data()
    return data.get("lang", "ru")


# --- КЛАВИАТУРЫ ---
def get_main_keyboard(lang: str = "ru"):
    m = MESSAGES[lang]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=m["btn_gen_seo"], callback_data="gen_seo")],
            [InlineKeyboardButton(text=m["btn_parse_yt"], callback_data="parse_yt")],
            [InlineKeyboardButton(text=m["btn_lang"], callback_data="select_lang")],
        ]
    )


def get_cancel_keyboard(lang: str = "ru"):
    m = MESSAGES[lang]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=m["btn_cancel"], callback_data="cancel")]
        ]
    )


def get_nav_keyboard(back_step: str, lang: str = "ru"):
    m = MESSAGES[lang]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=m["btn_back"], callback_data=f"back_{back_step}"),
                InlineKeyboardButton(text=m["btn_cancel"], callback_data="cancel"),
            ]
        ]
    )


def get_lang_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang_ru"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang_en"),
            ],
            [InlineKeyboardButton(text="❌ Cancel / Отмена", callback_data="cancel")],
        ]
    )


# --- ФУНКЦИЯ ПАРСИНГА YOUTUBE ---
async def fetch_youtube_tags(url: str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return None, "HTTP Status Error"
                html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        tags = [
            meta["content"]
            for meta in soup.find_all("meta", property="og:video:tag")
            if meta.get("content")
        ]
        title_meta = soup.find("meta", property="og:title")
        title = title_meta["content"] if title_meta else "Title Not Found"
        return {"title": title, "tags": tags}, None
    except Exception as e:
        return None, str(e)


# --- ВЫБОР И СМЕНА ЯЗЫКА ---
@dp.callback_query(F.data == "select_lang")
async def show_lang_selection(callback: types.CallbackQuery, state: FSMContext):
    lang = await get_user_lang(state)
    await callback.message.answer(
        MESSAGES[lang]["select_lang"],
        reply_markup=get_lang_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("set_lang_"))
async def set_language(callback: types.CallbackQuery, state: FSMContext):
    new_lang = callback.data.split("_")[-1]
    await state.update_data(lang=new_lang)

    await callback.message.answer(
        MESSAGES[new_lang]["lang_changed"],
        reply_markup=get_main_keyboard(new_lang),
    )
    await callback.answer()


# --- ОБРАБОТКА КОМАНД И НАВИГАЦИИ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    lang = await get_user_lang(state)
    await state.set_data({"lang": lang})  # Сохраняем языковой контекст
    await message.answer(
        MESSAGES[lang]["welcome"].format(name=message.from_user.first_name),
        reply_markup=get_main_keyboard(lang),
    )


@dp.callback_query(F.data == "cancel")
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    lang = await get_user_lang(state)
    await state.set_data({"lang": lang})
    await callback.message.answer(
        MESSAGES[lang]["cancelled"],
        reply_markup=get_main_keyboard(lang),
    )
    await callback.answer()


# --- СЦЕНАРИЙ 1: ГЕНЕРАЦИЯ SEO (ЧЕРЕЗ DEEPSEEK AI) ---
@dp.callback_query(F.data == "gen_seo")
async def start_seo_gen(callback: types.CallbackQuery, state: FSMContext):
    lang = await get_user_lang(state)
    await state.set_state(SEOForm.artist)
    await callback.message.answer(
        MESSAGES[lang]["seo_step1"],
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(lang),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_artist")
async def back_to_artist(callback: types.CallbackQuery, state: FSMContext):
    lang = await get_user_lang(state)
    await state.set_state(SEOForm.artist)
    await callback.message.answer(
        MESSAGES[lang]["seo_step1"],
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(lang),
    )
    await callback.answer()


@dp.message(SEOForm.artist)
async def process_artist(message: types.Message, state: FSMContext):
    lang = await get_user_lang(state)
    await state.update_data(artist=message.text)
    await state.set_state(SEOForm.genre)
    await message.answer(
        MESSAGES[lang]["seo_step2"],
        parse_mode="Markdown",
        reply_markup=get_nav_keyboard("artist", lang),
    )


@dp.callback_query(F.data == "back_genre")
async def back_to_genre(callback: types.CallbackQuery, state: FSMContext):
    lang = await get_user_lang(state)
    await state.set_state(SEOForm.genre)
    await callback.message.answer(
        MESSAGES[lang]["seo_step2"],
        parse_mode="Markdown",
        reply_markup=get_nav_keyboard("artist", lang),
    )
    await callback.answer()


@dp.message(SEOForm.genre)
async def process_genre(message: types.Message, state: FSMContext):
    lang = await get_user_lang(state)
    await state.update_data(genre=message.text)
    await state.set_state(SEOForm.bpm_key)
    await message.answer(
        MESSAGES[lang]["seo_step3"],
        parse_mode="Markdown",
        reply_markup=get_nav_keyboard("genre", lang),
    )


@dp.message(SEOForm.bpm_key)
async def process_bpm_key(message: types.Message, state: FSMContext):
    lang = await get_user_lang(state)
    await state.update_data(bpm_key=message.text)
    user_data = await state.get_data()
    
    # Сбрасываем шаги, но сохраняем выбранный язык
    await state.set_state(None)
    await state.set_data({"lang": lang})

    wait_msg = await message.answer(MESSAGES[lang]["seo_generating"])

    target_lang_str = "Russian" if lang == "ru" else "English"

    prompt = f"""
    You are a professional YouTube SEO specialist for beatmakers.
    Generate a complete SEO package for a Type Beat video.
    Language of response: {target_lang_str}.

    Input Data:
    - Artist/Style: {user_data['artist']}
    - Genre: {user_data['genre']}
    - BPM/Key: {user_data['bpm_key']}

    STRICT RULES:
    1. Always use the CURRENT year 2026 in all titles, tags, and description! Do NOT use past years (2023, 2024, 2025).

    OUTPUT FORMAT STRICTLY IN {target_lang_str}:

    📌 **TITLE:**
    (3 clickable title options with the year 2026)

    🏷 **TAGS:**
    (List of relevant tags separated by commas up to 400 characters including 2026, wrapped in monospace code block)

    📝 **DESCRIPTION:**
    (Description template with purchase link placeholder, timecodes, hashtags, and usage terms)
    """

    try:
        response = await ai_client.chat.completions.create(
            model="deepseek/deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

        seo_text = response.choices[0].message.content
        await wait_msg.delete()
        await message.answer(seo_text, parse_mode="Markdown")

    except Exception as e:
        await wait_msg.delete()
        await message.answer(
            MESSAGES[lang]["seo_error"].format(error=e),
            parse_mode="Markdown",
        )


# --- СЦЕНАРИЙ 2: РАЗБОР КОНКУРЕНТА ---
@dp.callback_query(F.data == "parse_yt")
async def start_parse_yt(callback: types.CallbackQuery, state: FSMContext):
    lang = await get_user_lang(state)
    await state.set_state(CompetitorForm.yt_link)
    await callback.message.answer(
        MESSAGES[lang]["parse_step1"],
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(lang),
    )
    await callback.answer()


@dp.message(CompetitorForm.yt_link)
async def process_yt_link(message: types.Message, state: FSMContext):
    lang = await get_user_lang(state)
    url = message.text.strip()
    if not ("youtube.com" in url or "youtu.be" in url):
        await message.answer(
            MESSAGES[lang]["parse_invalid_url"],
            reply_markup=get_cancel_keyboard(lang),
        )
        return

    await state.set_state(None)
    await state.set_data({"lang": lang})

    wait_msg = await message.answer(MESSAGES[lang]["parse_parsing"])

    data, error = await fetch_youtube_tags(url)
    await wait_msg.delete()

    if error or not data:
        await message.answer(
            MESSAGES[lang]["parse_error"].format(error=error),
            parse_mode="Markdown",
        )
        return

    tags_str = (
        ", ".join(data["tags"])
        if data["tags"]
        else MESSAGES[lang]["parse_no_tags"]
    )
    
    result_text = MESSAGES[lang]["parse_result"].format(
        title=data["title"],
        tags=tags_str,
        count=len(data["tags"]),
    )
    await message.answer(result_text, parse_mode="Markdown")


# --- ЗАПУСК БОТА ---
async def main():
    logging.basicConfig(level=logging.INFO)
    print("🚀 Multilingual DeepSeek SEO Bot successfully launched!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())