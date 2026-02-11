import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple, List
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

try:
    import rlottie_python
except ImportError:
    rlottie_python = None

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN missing in .env")
    RENDER_WIDTH: int = 512
    RENDER_HEIGHT: int = 512
    TARGET_FPS: int = 30
    FFMPEG_PRESET: str = 'slow'
    FFMPEG_CRF: str = '18'

class DependencyManager:
    @staticmethod
    def get_ffmpeg_path() -> Optional[str]:
        if shutil.which('ffmpeg'):
            return 'ffmpeg'
            
        local_appdata = os.environ.get('LOCALAPPDATA', '')
        if local_appdata:
            winget_path = Path(local_appdata) / 'Microsoft' / 'WinGet' / 'Packages'
            if winget_path.exists():
                try:
                    found = next(winget_path.rglob('ffmpeg.exe'), None)
                    if found:
                        return str(found)
                except Exception:
                    pass
        return None

class MediaConverter:
    @staticmethod
    async def _run_ffmpeg(cmd: List[str]) -> bool:
        logger.info(f"FFmpeg call: {' '.join(cmd)}")
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                logger.error(f"FFmpeg failed: {stderr.decode()}")
                return False
            return True
        except Exception as e:
            logger.error(f"Subprocess failed: {e}")
            return False

    @staticmethod
    async def render_tgs(tgs_path: str, output_dir: str) -> Tuple[Optional[str], int]:
        if not rlottie_python:
            return None, 0

        try:
            anim = rlottie_python.LottieAnimation.from_tgs(tgs_path)
            total_frames = anim.lottie_animation_get_totalframe()
            native_fps = int(anim.lottie_animation_get_framerate())
            
            step = 2
            out_fps = native_fps // step
            
            ptrn = os.path.join(output_dir, "frame_%04d.png")
            
            for i in range(0, total_frames, step):
                anim.save_frame(
                    ptrn % (i // step), 
                    frame_num=i, 
                    width=Config.RENDER_WIDTH, 
                    height=Config.RENDER_HEIGHT
                )
            return ptrn, out_fps
        except Exception as e:
            logger.error(f"Rlottie error: {e}")
            return None, 0

    @classmethod
    async def to_gif(cls, in_path: str, out_path: str, is_tgs: bool = False) -> bool:
        bin_path = DependencyManager.get_ffmpeg_path()
        if not bin_path:
            return False

        temp_dir = None
        try:
            cmd = [bin_path, '-y']
            
            if is_tgs:
                temp_dir = tempfile.mkdtemp()
                ptrn, fps = await cls.render_tgs(in_path, temp_dir)
                if not ptrn: return False
                cmd.extend(['-framerate', str(fps), '-i', ptrn])
            else:
                cmd.extend(['-i', in_path])

            vf = (
                f'fps={Config.TARGET_FPS},scale=512:512:flags=lanczos,'
                'pad=912:512:(ow-iw)/2:(oh-ih)/2:black,'
                'split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse'
            )
            cmd.extend(['-vf', vf, '-loop', '0', out_path])
            return await cls._run_ffmpeg(cmd)

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    @classmethod
    async def to_mp4(cls, in_path: str, out_path: str, is_tgs: bool = False) -> bool:
        bin_path = DependencyManager.get_ffmpeg_path()
        if not bin_path: return False

        temp_dir = None
        try:
            cmd = [bin_path, '-y']
            
            if is_tgs:
                temp_dir = tempfile.mkdtemp()
                ptrn, fps = await cls.render_tgs(in_path, temp_dir)
                if not ptrn: return False
                cmd.extend(['-framerate', str(fps), '-i', ptrn])
            else:
                cmd.extend(['-i', in_path])

            cmd.extend([
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',
                '-crf', Config.FFMPEG_CRF,
                '-preset', Config.FFMPEG_PRESET
            ])

            vf = (
                'scale=512:512:force_original_aspect_ratio=decrease,'
                'pad=912:512:(ow-iw)/2:(oh-ih)/2:black'
            )
            cmd.extend(['-vf', vf, out_path])
            return await cls._run_ffmpeg(cmd)

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "👋 Привет! Я бот для конвертации анимированных стикеров и премиум эмодзи.\n\n"
        "📤 Просто отправь мне:\n"
        "• Анимированный стикер\n"
        "• Премиум эмодзи\n\n"
        "Я конвертирую его в GIF или MP4 - выбери формат сам!\n\n"
        "💻 Разработчик : @imildar"
    )
    await update.message.reply_text(txt)

async def handle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    st = msg.sticker
    
    if not (st.is_animated or st.is_video):
        await msg.reply_text("❌ TGS or WebM required.", parse_mode='Markdown')
        return

    context.user_data['fid'] = st.file_id
    context.user_data['type'] = 'tgs' if st.is_animated else 'webm'
    
    kb = [[
        InlineKeyboardButton("🖼 GIF", callback_data='gif'),
        InlineKeyboardButton("🎬 MP4", callback_data='mp4')
    ]]
    await msg.reply_text("Choose format:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.entities: return

    for ent in update.message.entities:
        if ent.type == "custom_emoji":
            try:
                eid = ent.custom_emoji_id
                sets = await context.bot.get_custom_emoji_stickers([eid])
                
                if sets:
                    st = sets[0]
                    context.user_data['fid'] = st.file_id
                    context.user_data['type'] = 'tgs' if st.is_animated else 'webm'
                    
                    kb = [[
                        InlineKeyboardButton("🖼 GIF", callback_data='gif'),
                        InlineKeyboardButton("🎬 MP4", callback_data='mp4')
                    ]]
                    await update.message.reply_text(
                        "Emoji detected. Convert to:",
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                    return
            except Exception:
                pass
    await update.message.reply_text("Unsupported content.")

async def handle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    fid = context.user_data.get('fid')
    st_type = context.user_data.get('type')
    fmt = q.data
    
    if not fid:
        await q.edit_message_text("❌ Session expired.")
        return

    await q.edit_message_text(f"⏳ Processing {fmt.upper()}...")

    with tempfile.NamedTemporaryFile(suffix=f'.{st_type}', delete=False) as tf:
        in_p = tf.name
    out_p = in_p.replace(f'.{st_type}', f'.{fmt}')

    try:
        f_obj = await context.bot.get_file(fid)
        await f_obj.download_to_drive(in_p)
        
        ok = False
        is_tgs = (st_type == 'tgs')
        
        if fmt == 'gif':
            ok = await MediaConverter.to_gif(in_p, out_p, is_tgs)
        else:
            ok = await MediaConverter.to_mp4(in_p, out_p, is_tgs)
            
        if ok and os.path.exists(out_p):
            with open(out_p, 'rb') as f:
                method = q.message.reply_animation if fmt == 'gif' else q.message.reply_video
                await method(f, caption="✅ Done")
            await q.message.delete()
        else:
            await q.edit_message_text("❌ Conversion failed.")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        await q.edit_message_text("❌ System error.")
        
    finally:
        for p in [in_p, out_p]:
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except: pass

def main():
    if not DependencyManager.get_ffmpeg_path():
        print("🛑 FATAL: FFmpeg not found.")
        sys.exit(1)

    app = Application.builder().token(Config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_emoji))
    app.add_handler(CallbackQueryHandler(handle_cb))

    print("✅ Bot started.")
    app.run_polling()

if __name__ == '__main__':
    main()