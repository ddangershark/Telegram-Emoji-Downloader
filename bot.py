# ENG: Code made by Claude Sonnet 4.6 (free model)
# RU: Код сделан Claude Sonnet 4.6 (бесплатная модель)

import asyncio
import io
import logging
import os
import re
import zipfile
from pathlib import Path

import aiohttp
import aiofiles
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode, StickerFormat
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    BufferedInputFile,
    Message,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "Сюда ваш токен бота")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMOJI_ID_RE = re.compile(r"^\d{15,20}$")
EMOJI_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/addemoji/([A-Za-z0-9_]+)"
)


async def tgs_to_png_bytes(tgs_data: bytes, size: int = 512) -> bytes | None:
    """Конвертирует .tgs (gzip-lottie) -> PNG через rlottie_python."""
    import gzip

    try:
        with gzip.open(io.BytesIO(tgs_data)) as f:
            lottie_json = f.read().decode("utf-8")
    except Exception as e:
        log.error("tgs: не удалось распаковать gzip: %s", e)
        return None

    # --- rlottie_python ---
    try:
        import rlottie_python as rl
        import tempfile

        # Сохраняем JSON во временный файл — from_file поддерживается во всех версиях
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(lottie_json)
            tmp_path = tmp.name

        anim = rl.LottieAnimation.from_file(tmp_path)
        Path(tmp_path).unlink(missing_ok=True)

        w, h = anim.lottie_animation_get_size()
        frame = anim.render_pillow_frame(frame_num=0, width=size, height=size)
        buf = io.BytesIO()
        frame.save(buf, format="PNG")
        log.debug("tgs -> png via rlottie_python OK")
        return buf.getvalue()
    except Exception as e:
        log.error("rlottie_python failed: %s", e)

    return None


async def webp_to_png_bytes(webp_data: bytes) -> bytes:
    """Конвертирует WEBP → PNG через Pillow."""
    from PIL import Image

    img = Image.open(io.BytesIO(webp_data)).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def download_file(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    return buf.getvalue()


def get_sticker_kind(sticker) -> str:
    """Определяет тип стикера совместимо с разными версиями aiogram."""
    # aiogram 3.x новые версии — атрибут format
    try:
        fmt = sticker.format
        if str(fmt) in ("StickerFormat.ANIMATED", "animated"):
            return "animated"
        if str(fmt) in ("StickerFormat.VIDEO", "video"):
            return "video"
        return "static"
    except AttributeError:
        pass
    # aiogram 3.x старые версии — is_animated / is_video
    if getattr(sticker, "is_animated", False):
        return "animated"
    if getattr(sticker, "is_video", False):
        return "video"
    return "static"


async def sticker_to_png(bot: Bot, sticker) -> bytes | None:
    raw = await download_file(bot, sticker.file_id)
    kind = get_sticker_kind(sticker)

    if kind == "animated":   # .tgs
        return await tgs_to_png_bytes(raw)

    elif kind == "video":    # .webm
        try:
            import subprocess
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                webm_path = Path(tmp) / "s.webm"
                png_path = Path(tmp) / "s.png"
                webm_path.write_bytes(raw)
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(webm_path), "-vframes", "1", str(png_path)],
                    capture_output=True,
                    timeout=15,
                )
                if result.returncode == 0 and png_path.exists():
                    return png_path.read_bytes()
        except Exception as e:
            log.debug("ffmpeg failed: %s", e)
        return None

    else:   # static — .webp
        return await webp_to_png_bytes(raw)


# ---------------------------------------------------------------------------
# Bot handlers
# ---------------------------------------------------------------------------

dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 <b>Premium Emoji Downloader</b>\n\n"
        "Отправь мне:\n"
        "• Ссылку на набор эмодзи: <code>https://t.me/addemoji/SetName</code>\n"
        "• ID одного эмодзи: <code>0000000000000000000</code>\n\n"
        "Я скачаю все стикеры и пришлю тебе ZIP с PNG.",
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


# --- Обработка текстового сообщения (ссылка или ID) ---

@dp.message(F.text)
async def handle_text(message: Message, bot: Bot) -> None:
    text = message.text.strip()

    # Проверяем — ID одного эмодзи
    if EMOJI_ID_RE.match(text):
        await process_single_emoji_id(message, bot, int(text))
        return

    # Проверяем — ссылка на набор
    m = EMOJI_LINK_RE.search(text)
    if m:
        set_name = m.group(1)
        await process_sticker_set(message, bot, set_name)
        return

    await message.answer(
        "❓ Не понял запрос.\n"
        "Пришли ссылку <code>https://t.me/addemoji/SetName</code> "
        "или числовой ID эмодзи.",
        parse_mode=ParseMode.HTML,
    )


# --- Если пользователь прислал само премиум-эмодзи в сообщении ---

@dp.message(F.sticker)
async def handle_sticker(message: Message, bot: Bot) -> None:
    sticker = message.sticker
    if sticker.set_name:
        await process_sticker_set(message, bot, sticker.set_name)
    else:
        await process_single_sticker(message, bot, sticker)


# ---------------------------------------------------------------------------
# Processing logic
# ---------------------------------------------------------------------------

async def process_single_emoji_id(message: Message, bot: Bot, emoji_id: int) -> None:
    status = await message.answer("🔍 Получаю информацию об эмодзи...")

    try:
        stickers = await bot.get_custom_emoji_stickers([str(emoji_id)])
    except Exception as e:
        await status.edit_text(f"❌ Ошибка API: {e}")
        return

    if not stickers:
        await status.edit_text("❌ Эмодзи не найден.")
        return

    sticker = stickers[0]
    await status.edit_text("⬇️ Скачиваю и конвертирую...")

    png = await sticker_to_png(bot, sticker)
    if png is None:
        await status.edit_text(
            "⚠️ Не удалось конвертировать этот формат стикера.\n"
            "Видео-стикеры (.webm) требуют ffmpeg, анимированные — rlottie-python."
        )
        return

    name = f"emoji_{emoji_id}.png"
    await message.answer_document(
        BufferedInputFile(png, filename=name),
        caption=f"✅ <code>{emoji_id}</code>",
        parse_mode=ParseMode.HTML,
    )
    await status.delete()


async def process_single_sticker(message: Message, bot: Bot, sticker) -> None:
    status = await message.answer("⬇️ Скачиваю стикер...")
    png = await sticker_to_png(bot, sticker)
    if png is None:
        await status.edit_text("⚠️ Не удалось конвертировать формат.")
        return
    await message.answer_document(
        BufferedInputFile(png, filename="sticker.png"),
        caption="✅ Готово!",
    )
    await status.delete()


async def process_sticker_set(message: Message, bot: Bot, set_name: str) -> None:
    status = await message.answer(f"🔍 Загружаю набор <b>{set_name}</b>...", parse_mode=ParseMode.HTML)

    try:
        sticker_set = await bot.get_sticker_set(set_name)
    except Exception as e:
        await status.edit_text(f"❌ Набор не найден: <code>{e}</code>", parse_mode=ParseMode.HTML)
        return

    total = len(sticker_set.stickers)
    await status.edit_text(
        f"📦 Набор: <b>{sticker_set.title}</b>\n"
        f"Стикеров: {total}\n"
        f"⬇️ Скачиваю...",
        parse_mode=ParseMode.HTML,
    )

    zip_buf = io.BytesIO()
    success = 0
    failed = 0

    with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, sticker in enumerate(sticker_set.stickers, 1):
            try:
                png = await sticker_to_png(bot, sticker)
                if png:
                    emoji_safe = "".join(
                        c if c.isascii() and c.isalnum() else "_"
                        for c in (sticker.emoji or "x")
                    )
                    filename = f"{i:03d}_{emoji_safe}_{sticker.file_unique_id}.png"
                    zf.writestr(filename, png)
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                log.warning("Failed sticker %s: %s", sticker.file_id, e)
                failed += 1

            # Обновляем прогресс каждые 10 стикеров
            if i % 10 == 0 or i == total:
                try:
                    await status.edit_text(
                        f"⬇️ Прогресс: {i}/{total}...",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

    zip_buf.seek(0)
    zip_name = f"{set_name}.zip"

    caption = (
        f"✅ <b>{sticker_set.title}</b>\n"
        f"Сохранено: {success}/{total}"
        + (f"\nПропущено: {failed}" if failed else "")
    )

    await message.answer_document(
        BufferedInputFile(zip_buf.read(), filename=zip_name),
        caption=caption,
        parse_mode=ParseMode.HTML,
    )
    await status.delete()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    bot = Bot(token=BOT_TOKEN)
    log.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
