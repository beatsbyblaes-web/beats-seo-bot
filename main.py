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

# Загружаем переменные окружения из .env
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


# --- СОСТОЯНИЯ (FSM) ---
class SEOForm(StatesGroup):
    artist = State()
    genre = State()
    bpm_key = State()


class CompetitorForm(StatesGroup):
    yt_link = State()


# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Сгенерировать SEO", callback_data="gen_seo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔍 Разобрать конкурента", callback_data="parse_yt"
                )
            ],
        ]
    )


def get_cancel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
        ]
    )


def get_nav_keyboard(back_step: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data=f"back_{back_step}"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"),
            ]
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
                    return None, "Не удалось открыть ссылку."
                html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        tags = [
            meta["content"]
            for meta in soup.find_all("meta", property="og:video:tag")
            if meta.get("content")
        ]
        title_meta = soup.find("meta", property="og:title")
        title = (
            title_meta["content"]
            if title_meta
            else "Название не найдено"
        )
        return {"title": title, "tags": tags}, None
    except Exception as e:
        return None, str(e)


# --- ОБРАБОТКА КОМАНД И НАВИГАЦИИ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Я AI-помощник для битмейкеров. Помогу составить SEO или разобрать конкурентов.\n"
        "Выбери нужное действие:",
        reply_markup=get_main_keyboard(),
    )


@dp.callback_query(F.data == "cancel")
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "❌ Действие отменено. Выбери нужное действие:",
        reply_markup=get_main_keyboard(),
    )
    await callback.answer()


# --- СЦЕНАРИЙ 1: ГЕНЕРАЦИЯ SEO (ЧЕРЕЗ DEEPSEEK AI) ---
@dp.callback_query(F.data == "gen_seo")
async def start_seo_gen(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SEOForm.artist)
    await callback.message.answer(
        "🎤 **Шаг 1/3:** Введи имя артиста или стиль (например: `Drake x Travis Scott`):",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_artist")
async def back_to_artist(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SEOForm.artist)
    await callback.message.answer(
        "🎤 **Шаг 1/3:** Введи имя артиста или стиль (например: `Drake x Travis Scott`):",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@dp.message(SEOForm.artist)
async def process_artist(message: types.Message, state: FSMContext):
    await state.update_data(artist=message.text)
    await state.set_state(SEOForm.genre)
    await message.answer(
        "🥁 **Шаг 2/3:** Укажи жанр/вайб (например: `Dark Trap`, `Jersey Club`):",
        parse_mode="Markdown",
        reply_markup=get_nav_keyboard("artist"),
    )


@dp.callback_query(F.data == "back_genre")
async def back_to_genre(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SEOForm.genre)
    await callback.message.answer(
        "🥁 **Шаг 2/3:** Укажи жанр/вайб (например: `Dark Trap`, `Jersey Club`):",
        parse_mode="Markdown",
        reply_markup=get_nav_keyboard("artist"),
    )
    await callback.answer()


@dp.message(SEOForm.genre)
async def process_genre(message: types.Message, state: FSMContext):
    await state.update_data(genre=message.text)
    await state.set_state(SEOForm.bpm_key)
    await message.answer(
        "🎹 **Шаг 3/3:** Укажи BPM и тональность (например: `140 BPM / C# Minor`):",
        parse_mode="Markdown",
        reply_markup=get_nav_keyboard("genre"),
    )


@dp.message(SEOForm.bpm_key)
async def process_bpm_key(message: types.Message, state: FSMContext):
    await state.update_data(bpm_key=message.text)
    user_data = await state.get_data()
    await state.clear()

    wait_msg = await message.answer(
        "🤖 Нейросеть генерирует уникальный SEO-пакет..."
    )

    prompt = f"""
    Ты профессиональный YouTube SEO-специалист для битмейкеров.
    Сгенерируй готовый SEO-пакет для Type Beat видео.

    Входные данные:
    - Артист/Стиль: {user_data['artist']}
    - Жанр: {user_data['genre']}
    - BPM/Тональность: {user_data['bpm_key']}

    СТРОГИЕ ПРАВИЛА:
    1. Использовать ТОЛЬКО текущий 2026 год во всех заголовках, тегах и описании! Запрещено использовать прошлые года (2023, 2024, 2025).

    Выдай ответ СТРОГО в формате:

    📌 **TITLE (Варианты заголовка):**
    (3 варианта кликабельного названия с 2026 годом)

    🏷 **TAGS (Теги для поля YouTube):**
    (Список релевантных тегов через запятую до 400 символов с упоминанием 2026 года, обернутый в моноширинный код)

    📝 **DESCRIPTION (Шаблон описания):**
    (Описание со ссылкой на покупку, таймкодами, хештегами и условиями использования)
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
            f"❌ Ошибка генерации AI! Проверь ключ OpenRouter.\n\n`{e}`",
            parse_mode="Markdown",
        )


# --- СЦЕНАРИЙ 2: РАЗБОР КОНКУРЕНТА ---
@dp.callback_query(F.data == "parse_yt")
async def start_parse_yt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(CompetitorForm.yt_link)
    await callback.message.answer(
        "🔗 **Отправь ссылку на YouTube-видео** конкурента:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@dp.message(CompetitorForm.yt_link)
async def process_yt_link(message: types.Message, state: FSMContext):
    url = message.text.strip()
    if not ("youtube.com" in url or "youtu.be" in url):
        await message.answer(
            "❌ Это не похоже на ссылку YouTube. Попробуй еще раз!",
            reply_markup=get_cancel_keyboard(),
        )
        return

    await state.clear()
    wait_msg = await message.answer(
        "🔍 Парсю страницу видео и вытаскиваю скрытые теги..."
    )

    data, error = await fetch_youtube_tags(url)
    await wait_msg.delete()

    if error or not data:
        await message.answer(
            f"❌ Не удалось разобрать видео. Ошибка: `{error}`",
            parse_mode="Markdown",
        )
        return

    tags_str = (
        ", ".join(data["tags"])
        if data["tags"]
        else "У этого видео нет тегов (или они скрыты)."
    )
    result_text = f"""
🎬 **Название видео:**
*{data['title']}*

🔑 **Реальные теги видео (готовы к копированию):**
`{tags_str}`

📊 **Всего тегов:** {len(data['tags'])} шт.
    """
    await message.answer(result_text, parse_mode="Markdown")


# --- ЗАПУСК БОТА ---
async def main():
    logging.basicConfig(level=logging.INFO)
    print("🚀 Бот с нейросетью DeepSeek и парсером успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())