import asyncio
import os
import uuid
import threading
import time

# Set up event loop before importing Pyrogram for Python 3.10+ compatibility
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from flask import Flask, request, jsonify, render_template
from pyrogram import Client
from telethon import TelegramClient
from telethon.sessions import StringSession

app = Flask(__name__)

REDIS_URL = os.environ.get(
    "REDIS_URL",
    "rediss://default:gQAAAAAAAXopAAIgcDJhMGZhMDQzMTRhNGI0NjdlOTI0YTJiNTlkOGY2MTBlNQ@viable-goat-96809.upstash.io:6379"
)

clients = {}

def start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=start_loop, args=(loop,), daemon=True).start()

def run_async(coro):
    """Run a coroutine in the background event loop and wait for the result."""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=60) 
    except Exception as e:
        raise e

def is_2fa_required(error):
    """Normalize the different 2FA exception names used by Telegram libraries."""
    name = error.__class__.__name__.lower()
    message = str(error).lower()
    return "sessionpasswordneeded" in name or "session password needed" in message

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/send_code", methods=["POST"])
def send_code():
    data = request.json
    api_id = int(data.get("api_id"))
    api_hash = data.get("api_hash")
    phone = data.get("phone", "").replace(" ", "")

    library = data.get("library", "pyrogram")

    session_id = str(uuid.uuid4())

    async def _logic():
        if library == "telethon":
            client = TelegramClient(StringSession(), api_id, api_hash)
            await client.connect()
            sent_code = await client.send_code_request(phone)
            clients[session_id] = {"client": client, "library": "telethon", "created": time.time()}
            return {"phone_code_hash": sent_code.phone_code_hash, "session_id": session_id}
        else:
            client = Client(
                f"session_{session_id}",
                api_id=api_id,
                api_hash=api_hash,
                in_memory=True
            )
            await client.connect()
            sent_code = await client.send_code(phone)
            clients[session_id] = {"client": client, "library": "pyrogram", "created": time.time()}
            return {"phone_code_hash": sent_code.phone_code_hash, "session_id": session_id}

    try:
        result = run_async(_logic())
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    session_id = data.get("session_id")
    bot_token = data.get("bot_token")
    phone = data.get("phone", "").replace(" ", "")
    phone_code = data.get("phone_code", "").replace(" ", "")
    phone_code_hash = data.get("phone_code_hash")
    password = data.get("password", "")
    library = data.get("library", "pyrogram")

    if not session_id and not bot_token:
        return jsonify({"error": "Missing session_id or bot_token"}), 400

    async def _logic():
        if bot_token:
            api_id = int(data.get("api_id"))
            api_hash = data.get("api_hash")
            if library == "telethon":
                client = TelegramClient(StringSession(), api_id, api_hash)
                await client.connect()
                try:
                    await client.sign_in(bot_token=bot_token)
                    session_string = client.session.save()
                    return {"session_string": session_string}
                finally:
                    await client.disconnect()
            else:
                client = Client(
                    f"session_bot_{uuid.uuid4()}",
                    api_id=api_id,
                    api_hash=api_hash,
                    in_memory=True
                )
                await client.connect()
                try:
                    await client.sign_in_bot(bot_token)
                    session_string = await client.export_session_string()
                    return {"session_string": session_string}
                finally:
                    await client.disconnect()
        
        session_data = clients.get(session_id)
        if not session_data:
            raise Exception("Session not found or expired. Please try again.")
        
        client = session_data["client"]
        lib = session_data["library"]

        keep_client = False
        try:
            if password:
                if lib == "telethon":
                    await client.sign_in(password=password)
                    session_string = client.session.save()
                else:
                    await client.check_password(password)
                    session_string = await client.export_session_string()
                return {"session_string": session_string}

            if phone and phone_code and phone_code_hash:
                try:
                    if lib == "telethon":
                        await client.sign_in(phone, phone_code, phone_code_hash=phone_code_hash)
                        session_string = client.session.save()
                    else:
                        await client.sign_in(phone, phone_code_hash, phone_code)
                        session_string = await client.export_session_string()
                    return {"session_string": session_string}
                except Exception as error:
                    if not is_2fa_required(error):
                        raise
                    keep_client = True
                    return {"needs_2fa": True, "session_id": session_id}
            else:
                raise Exception("Missing login details")
            
        finally:
            if not keep_client:
                await client.disconnect()
                if session_id in clients:
                    del clients[session_id]

    try:
        result = run_async(_logic())
        return jsonify(result)
    except Exception as e:
        if session_id in clients and not data.get("password"):
            try:
                client = clients[session_id]["client"]
                run_async(client.disconnect())
            except:
                pass
            del clients[session_id]
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(debug=True, port=8000)

