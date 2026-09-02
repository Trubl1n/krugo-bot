import asyncio
import logging
import sys
import tempfile
import os
import uuid

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from config import BOT_TOKEN

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Ограничение на количество одновременно обрабатываемых видео
CONCURRENCY_LIMIT = 2
video_semaphore = None


async def convert_to_video_note(input_path: str, output_path: str) -> bool:
    """
    Конвертирует исходное видео в формат кружка (квадрат 640x640, до 60 сек, x264/aac).
    """
    # Фильтры: обрезаем до квадрата по минимальной стороне и масштабируем до 640x640.
    # setsar=1 нужен, чтобы избежать проблем с пропорциями пикселей.
    vf_filters = "crop='min(iw,ih)':'min(iw,ih)',scale=640:640,setsar=1"
    
    cmd = [
        "ffmpeg",
        "-y",                 # Перезаписать выходной файл, если он существует
        "-i", input_path,     # Входной файл
        "-t", "60",           # Обрезка до 1 минуты (ограничение Telegram)
        "-vf", vf_filters,    # Видео фильтры
        "-c:v", "libx264",    # Кодек видео
        "-preset", "fast",    # Пресет кодирования для скорости
        "-profile:v", "main", # Профиль совместимости
        "-c:a", "aac",        # Аудио кодек
        "-b:a", "128k",       # Битрейт аудио
        "-movflags", "+faststart", 
        output_path
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logging.error(f"FFmpeg error: {stderr.decode('utf-8', errors='ignore')}")
            return False
        return True
    except FileNotFoundError:
        logging.error("FFmpeg не найден в системе. Убедитесь, что ffmpeg установлен и добавлен в PATH.")
        raise
    except Exception as e:
        logging.error(f"Ошибка во время конвертации видео: {e}")
        return False


@dp.message(Command("start"))
async def start_handler(message: types.Message):
    """
    Хендлер на команду /start.
    Отправляет подробное приветствие и инлайн-кнопку со ссылкой на канал.
    """
    username = message.from_user.username or "No_username"
    logging.info(f"User {message.from_user.id} (@{username}) started the bot.")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Наш канал", url="https://t.me/gitgems")]
    ])
    
    welcome_text = (
        "👋 Привет! Я бот для удобной и быстрой конвертации любых видео в Telegram-кружки (video note).\n\n"
        "🎬 <b>Что я умею:</b>\n"
        "— Принимаю обычные видео и видео, отправленные как файл (документ).\n"
        "— Автоматически обрезаю видео до красивого квадрата по центру.\n"
        "— Самостоятельно ограничиваю длину до 60 секунд (требование Telegram для кружков).\n\n"
        "✨ <b>Мои главные плюсы:</b>\n"
        "— Абсолютно бесплатно!\n"
        "— Никакой рекламы и водяных знаков.\n"
        "— <b>Никаких обязательных подписок</b> и прочей назойливой фигни! Отправляешь видео — получаешь кружок.\n\n"
        "👇 Просто отправь мне видео, и я сразу начну работу!\n\n"
        "<i>(А если тебе интересны полезные проекты, ИИ-инструменты и фичи для работы — можешь заглянуть на наш канал по кнопке ниже. Это исключительно по желанию!)</i>"
    )
    
    # Используем parse_mode="HTML" для форматирования жирного шрифта и курсива
    await message.answer(welcome_text, reply_markup=keyboard, parse_mode="HTML")


# Хендлер ловит как стандартные видео, так и документы с MIME-типом, начинающимся на video/
@dp.message(F.video | (F.document & F.document.mime_type.startswith('video/')))
async def video_handler(message: types.Message):
    """
    Хендлер для входящих видео.
    Осуществляет скачивание, конвертацию и отправку видео-кружка.
    Использует семафор для ограничения одновременной обработки.
    """
    global video_semaphore
    if video_semaphore is None:
        video_semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        
    # Определяем объект файла (видео или документ)
    file_obj = message.video or message.document
    
    logging.info(f"User {message.from_user.id} sent a video/document. Size: {file_obj.file_size} bytes.")
    
    # Проверка размера (ограничение Telegram Bot API на скачивание — 20 МБ)
    MAX_FILE_SIZE = 20 * 1024 * 1024
    if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE:
        logging.info(f"User {message.from_user.id} file is too large ({file_obj.file_size} bytes). Rejected.")
        await message.answer("❌ Файл слишком большой. Бот поддерживает скачивание файлов размером не более 20 МБ.")
        return

    # Проверка длительности видео, если метаданные доступны
    duration = getattr(file_obj, 'duration', None)
    if duration and duration > 60:
        logging.info(f"User {message.from_user.id} video is longer than 60s ({duration}s). Warning sent.")
        await message.answer("⚠️ Ваше видео длиннее 1 минуты. Оно будет автоматически обрезано до первых 60 секунд.")

    # Сообщение с клавиатурой, которое видит пользователь в очереди
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Наш канал", url="https://t.me/gitgems")]
    ])
    
    status_msg = await message.answer(
        "⏳ Ваше видео встало в очередь на обработку. Пожалуйста, подождите немного.\n\n"
        "💡 А пока заглядывайте на наш канал: там регулярно выходят интересные проекты, "
        "инструменты с ИИ для жизни и работы, а также полезные фичи!",
        reply_markup=keyboard
    )
    logging.info(f"User {message.from_user.id} video queued.")
    
    # Ожидаем своей очереди на обработку
    async with video_semaphore:
        try:
            # Обновляем статус, оставляем клавиатуру и промо-текст во время обработки
            await status_msg.edit_text(
                "🔄 Обработка видео...\n\n"
                "💡 А пока видео обрабатывается, заглядывайте на наш канал: там регулярно выходят интересные проекты, "
                "инструменты с ИИ для жизни и работы, а также полезные фичи!",
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Failed to edit status message for user {message.from_user.id}: {e}")

        # Генерируем уникальные имена для временных файлов
        input_filename = f"input_{message.from_user.id}_{uuid.uuid4().hex}.mp4"
        output_filename = f"output_{message.from_user.id}_{uuid.uuid4().hex}.mp4"
        
        input_path = os.path.join(tempfile.gettempdir(), input_filename)
        output_path = os.path.join(tempfile.gettempdir(), output_filename)
        
        try:
            logging.info(f"Downloading video from user {message.from_user.id}...")
            # Скачиваем видео файл
            await bot.download(file_obj, destination=input_path)
            
            logging.info(f"Converting video for user {message.from_user.id}...")
            # Запускаем конвертацию ffmpeg через subprocess
            success = await convert_to_video_note(input_path, output_path)
            
            if not success:
                logging.error(f"Video conversion failed for user {message.from_user.id}.")
                await status_msg.edit_text("❌ Произошла ошибка при конвертации видео. Возможно, видео повреждено или неподдерживаемого формата.")
                return

            logging.info(f"Sending video note to user {message.from_user.id}...")
            # Отправляем сконвертированный файл как кружок
            video_note = FSInputFile(output_path)
            await message.answer_video_note(video_note)
            
            # Удаляем сообщение со статусом обработки и клавиатурой
            await status_msg.delete()
            
            logging.info(f"Successfully processed and sent video note to user {message.from_user.id}.")
            
        except FileNotFoundError:
            logging.error(f"User {message.from_user.id} encountered an error: FFmpeg not found.")
            await status_msg.edit_text("❌ Системная ошибка: утилита `ffmpeg` не установлена на сервере. Обратитесь к администратору.")
        except Exception as e:
            logging.error(f"Unexpected error for user {message.from_user.id}: {e}")
            try:
                await status_msg.edit_text("❌ Произошла непредвиденная ошибка при обработке видео.")
            except:
                pass
        finally:
            # Очистка временных файлов
            if os.path.exists(input_path):
                try:
                    os.remove(input_path)
                except Exception as e:
                    logging.error(f"Failed to delete {input_path}: {e}")
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception as e:
                    logging.error(f"Failed to delete {output_path}: {e}")


@dp.message(F.text)
async def text_handler(message: types.Message):
    """
    Базовый хендлер для текстовых сообщений.
    """
    await message.answer(
        "Я работаю только с видео! 📹\n"
        "Пожалуйста, отправь видеофайл или обычное видео, чтобы я превратил его в кружок. "
        "(Для подробностей отправь /start)"
    )


async def main():
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout
    )
    logging.info("Starting bot...")
    
    # Запуск polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped")
