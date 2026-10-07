import os
import re
import sqlite3
from pyrogram import Client, filters
from pyrogram.types import Message

# Mengambil konfigurasi dari Environment Variables Railway secara otomatis
API_ID = int(os.getenv("API_ID", "31846368"))
API_HASH = os.getenv("API_HASH", "c02139db5e8bc7a6252b2375e4be6dac")
BOT_TOKEN = os.getenv("BOT_TOKEN", "MASUKKAN_BOT_TOKEN_ANDA")
DB_FILE = "users_session.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS sessions (user_id INTEGER PRIMARY KEY, session_string TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS user_states (user_id INTEGER PRIMARY KEY, state TEXT, phone TEXT, phone_code_hash TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS temp_sessions (user_id INTEGER PRIMARY KEY, session_string TEXT)")
    conn.commit()
    conn.close()

init_db()

app = Client("my_restricted_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

@app.on_message(filters.command("start"))
async def start_handler(client, message: Message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
    conn.commit()
    
    cursor = conn.execute("SELECT session_string FROM sessions WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row and row[0]:
        await message.reply("✅ **Akun Anda sudah terhubung!**\n\nSilakan kirimkan tautan pesan Telegram terbatas (`t.me/c/...`) yang ingin didownload.")
    else:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("REPLACE INTO user_states (user_id, state) VALUES (?, ?)", (user_id, "WAITING_PHONE"))
        conn.commit()
        conn.close()
        await message.reply("👋 **Selamat datang!**\n\nSilakan kirimkan nomor telepon akun Telegram Anda (contoh format: `+628123456789`):")

@app.on_message(filters.text & ~filters.command(["start"]))
async def message_handler(client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.execute("SELECT state, phone, phone_code_hash FROM user_states WHERE user_id = ?", (user_id,))
    state_row = cursor.fetchone()
    conn.close()

    state = state_row[0] if state_row else None
    phone = state_row[1] if state_row else None
    code_hash = state_row[2] if state_row else None

    if state == "WAITING_PHONE":
        sent_msg = await message.reply("⏳ Memproses nomor telepon...")
        try:
            temp_client = Client(f"temp_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
            await temp_client.connect()
            sent_code = await temp_client.send_code(text)
            temp_session_str = await temp_client.export_session_string()
            await temp_client.disconnect()

            conn = sqlite3.connect(DB_FILE)
            conn.execute("REPLACE INTO temp_sessions (user_id, session_string) VALUES (?, ?)", (user_id, temp_session_str))
            conn.execute("REPLACE INTO user_states (user_id, state, phone, phone_code_hash) VALUES (?, ?, ?, ?)", (user_id, "WAITING_CODE", text, sent_code.phone_code_hash))
            conn.commit()
            conn.close()
            
            await sent_msg.edit("📨 **Kode OTP telah dikirim.**\n\nSilakan masukkan kode OTP tersebut (mendukung spasi, contoh: `1 2 3 4 5`):")
        except Exception as e:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await sent_msg.edit(f"❌ Error: `{e}`\n\nKetik /start untuk mengulang.")

    elif state == "WAITING_CODE":
        code = text.replace(" ", "")
        sent_msg = await message.reply("⏳ Memverifikasi OTP...")
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.execute("SELECT session_string FROM temp_sessions WHERE user_id = ?", (user_id,))
            t_row = cursor.fetchone()
            conn.close()

            if not t_row:
                await sent_msg.edit("❌ Sesi kedaluwarsa. Ketik /start untuk mengulang.")
                return

            temp_client = Client(f"temp_{user_id}", api_id=API_ID, api_hash=API_HASH, session_string=t_row[0], in_memory=True)
            await temp_client.connect()
            
            try:
                await temp_client.sign_in(phone_number=phone, phone_code_hash=code_hash, phone_code=code)
                session_string = await temp_client.export_session_string()
                
                conn = sqlite3.connect(DB_FILE)
                conn.execute("REPLACE INTO sessions (user_id, session_string) VALUES (?, ?)", (user_id, session_string))
                conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM temp_sessions WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
                
                await temp_client.disconnect()
                await sent_msg.edit("🎉 **Login Berhasil!**\n\nKirim tautan `t.me/c/...` untuk mendownload.")
            except Exception as auth_err:
                await temp_client.disconnect()
                if "SessionPasswordNeeded" in str(auth_err) or "PASSWORD" in str(auth_err).upper():
                    conn = sqlite3.connect(DB_FILE)
                    conn.execute("REPLACE INTO user_states (user_id, state, phone, phone_code_hash) VALUES (?, ?, ?, ?)", (user_id, "WAITING_PASSWORD", phone, code_hash))
                    conn.commit()
                    conn.close()
                    await sent_msg.edit("🔒 **Verifikasi 2FA Aktif.**\n\nSilakan masukkan Password 2FA Anda:")
                else:
                    raise auth_err
        except Exception as e:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await sent_msg.edit(f"❌ Verifikasi gagal: `{e}`\n\nKetik /start untuk mengulang.")

    elif state == "WAITING_PASSWORD":
        sent_msg = await message.reply("⏳ Memverifikasi password 2FA...")
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.execute("SELECT session_string FROM temp_sessions WHERE user_id = ?", (user_id,))
            t_row = cursor.fetchone()
            conn.close()

            temp_client = Client(f"temp_{user_id}", api_id=API_ID, api_hash=API_HASH, session_string=t_row[0] if t_row else None, in_memory=True)
            await temp_client.connect()
            
            await temp_client.check_password(password=text)
            session_string = await temp_client.export_session_string()
            
            conn = sqlite3.connect(DB_FILE)
            conn.execute("REPLACE INTO sessions (user_id, session_string) VALUES (?, ?)", (user_id, session_string))
            conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM temp_sessions WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()

            await temp_client.disconnect()
            await sent_msg.edit("🎉 **Login 2FA Berhasil!**\n\nKirim tautan `t.me/c/...` untuk didownload.")
        except Exception as e:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            await sent_msg.edit(f"❌ Password salah: `{e}`\n\nKetik /start untuk mengulang.")

    elif "t.me/" in text:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.execute("SELECT session_string FROM sessions WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()

        if not row or not row[0]:
            await message.reply("⚠️ Anda belum menghubungkan akun. Ketik /start terlebih dahulu.")
            return

        saved_session = row[0]
        status_msg = await message.reply("📥 Memproses tautan...")
        try:
            match_private = re.search(r"t\.me/c/(\d+)/(\d+)", text)
            match_public = re.search(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", text)

            if match_private:
                chat_id = int(f"-100{match_private.group(1)}")
                msg_id = int(match_private.group(2))
            elif match_public:
                chat_id = match_public.group(1)
                msg_id = int(match_public.group(2))
            else:
                await status_msg.edit("❌ Format tautan salah.")
                return

            async with Client(f"us_{user_id}", api_id=API_ID, api_hash=API_HASH, session_string=saved_session, in_memory=True) as u_client:
                target_msg = await u_client.get_messages(chat_id, msg_id)
                if target_msg and target_msg.media:
                    await status_msg.edit("📥 Mengunduh media...")
                    file_path = await u_client.download_media(target_msg)
                    
                    await status_msg.edit("📤 Mengirim file...")
                    await message.reply_document(file_path, caption=target_msg.caption or "")
                    
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                    await status_msg.delete()
                else:
                    await status_msg.edit("❌ Media tidak ditemukan.")
        except Exception as e:
            await status_msg.edit(f"❌ Error: `{e}`")

print("Bot berjalan di Railway...")
app.run()
  
