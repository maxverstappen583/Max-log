#!/usr/bin/env python3
import os
import json
import threading
import asyncio
import re
from datetime import datetime, timezone

from flask import Flask, request, jsonify
import discord
from discord.ext import commands

DATA_DIR = "data"
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")
COMMANDS_PATH = os.path.join(DATA_DIR, "commands.txt")

DEFAULT_CONFIG = {
    "token": "YOUR_DISCORD_BOT_TOKEN_HERE",
    "status_channel_id": None,
    "status_message_id": None,
    "refresh_seconds": 60,
    "owner_id": 1319292111325106296,
    "report_secret": "CHANGE_THIS_SECRET",
    "green_emoji": "1301233963301474316",
    "yellow_emoji":"1301233958117445704",
    "red_emoji":"1301233955663646750",
    "silent_commands": ["clear"]
}

file_lock = threading.Lock()

def ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(COMMANDS_PATH):
        sample = "avatar,ban,blacklist,clear,help,hi,kick,logs,say,ping,pong,usage,set_status_refresh,get_status_refresh"
        with file_lock:
            with open(COMMANDS_PATH, "w", encoding="utf-8") as f:
                f.write(sample)
    if not os.path.exists(CONFIG_PATH):
        with file_lock:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4)
    if not os.path.exists(STATUS_PATH):
        cmds = load_commands()
        initial = {}
        for c in cmds:
            initial[c] = {"last_success": True, "last_latency": None, "last_updated": None}
        with file_lock:
            with open(STATUS_PATH, "w", encoding="utf-8") as f:
                json.dump(initial, f, indent=4)

def load_config():
    with file_lock:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

def save_config(cfg):
    with file_lock:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)

def load_commands():
    if not os.path.exists(COMMANDS_PATH):
        return []
    with file_lock:
        with open(COMMANDS_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    parts = sorted(parts, key=lambda s: s.lower())
    return parts

def load_status():
    if not os.path.exists(STATUS_PATH):
        return {}
    with file_lock:
        with open(STATUS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

def save_status(status):
    with file_lock:
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=4)

def update_command_status(command_raw, success, latency_ms=None, timestamp=None):
    command = command_raw.lstrip("/").strip()
    status = load_status()
    if command not in status:
        status[command] = {"last_success": True, "last_latency": None, "last_updated": None}
    status[command]['last_success'] = bool(success)
    status[command]['last_latency'] = None if latency_ms is None else int(latency_ms)
    status[command]['last_updated'] = timestamp or datetime.now(timezone.utc).isoformat()
    save_status(status)

def parse_time_input(s: str):
    s = s.strip().lower()
    m = re.match(r'^(\d+)\s*([smhd])?$', s)
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2) or 's'
    if unit == 's':
        return value
    if unit == 'm':
        return value * 60
    if unit == 'h':
        return value * 3600
    if unit == 'd':
        return value * 86400
    return None

def human_readable_seconds(sec: int):
    if sec % 86400 == 0:
        return f"{sec // 86400}d"
    if sec % 3600 == 0:
        return f"{sec // 3600}h"
    if sec % 60 == 0:
        return f"{sec // 60}m"
    return f"{sec}s"

# --- Flask app for receiving reports from Maxy ---
app = Flask(__name__)
ensure_files()  # make sure data files exist

@app.route('/report', methods=['POST'])
def report():
    cfg = load_config()
    token = request.headers.get('X-Report-Token') or request.args.get('token')
    if token != cfg.get('report_secret'):
        return jsonify({'ok': False, 'error': 'invalid token'}), 401
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'ok': False, 'error': 'invalid json'}), 400
    cmd = data.get('command')
    if not cmd:
        return jsonify({'ok': False, 'error': 'missing command'}), 400
    success = data.get('success', True)
    latency = data.get('latency_ms', None)
    timestamp = data.get('timestamp', None)
    update_command_status(cmd, success, latency, timestamp)
    return jsonify({'ok': True})

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'ok': True, 'time': datetime.now(timezone.utc).isoformat()})

# --- Discord bot ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)
status_task_handle = None

async def build_status_embed(countdown_seconds):
    cfg = load_config()
    cmds = load_commands()
    status = load_status()
    embed = discord.Embed(title=f"⚡ Command Status | Updating in: {countdown_seconds} seconds 📶",
                          color=0x1abc9c, timestamp=datetime.now(timezone.utc))
    show = cmds[:15]
    for cmd in show:
        entry = status.get(cmd, {})
        last_success = entry.get('last_success', True)
        last_latency = entry.get('last_latency', None)
        percent = "100%" if last_success else "0%"
        field_name = f"🔹 | /{cmd}: {percent}"
        if not last_success:
            field_value = "(Ping: — | ❌ Failed last run)"
        else:
            if cmd in cfg.get('silent_commands', []):
                green = f"<:greenwifi:{cfg.get('green_emoji')}>"
                field_value = f"(Ping: — | {green} Executes silently)"
            else:
                if last_latency is None:
                    green = f"<:greenwifi:{cfg.get('green_emoji')}>"
                    field_value = f"(Ping: — | {green})"
                else:
                    if last_latency < 50:
                        emo = f"<:greenwifi:{cfg.get('green_emoji')}>"
                    elif last_latency <= 150:
                        emo = f"<:yellowwifi:{cfg.get('yellow_emoji')}>"
                    else:
                        emo = f"<:redwifi:{cfg.get('red_emoji')}>"
                    field_value = f"(Ping: {last_latency} ms | {emo})"
        embed.add_field(name=field_name, value=field_value, inline=False)
    total = len(cmds)
    embed.set_footer(text=f"Only 15 commands are displayed here since Maxy has too many ({total} total).")
    return embed

async def status_loop():
    await bot.wait_until_ready()
    cfg = load_config()
    channel_id = cfg.get('status_channel_id')
    refresh = int(cfg.get('refresh_seconds', 60))
    countdown = refresh
    channel = None
    message = None
    # try to fetch existing message
    if channel_id:
        try:
            channel = bot.get_channel(int(channel_id))
            if channel and cfg.get('status_message_id'):
                try:
                    message = await channel.fetch_message(int(cfg.get('status_message_id')))
                except Exception:
                    message = None
        except Exception:
            channel = None
    # if no channel configured, do nothing until it's set
    while not bot.is_closed():
        cfg = load_config()
        if not cfg.get('status_channel_id'):
            await asyncio.sleep(5)
            continue
        # ensure channel object is valid
        if not channel or channel.id != int(cfg.get('status_channel_id')):
            channel = bot.get_channel(int(cfg.get('status_channel_id')))
            message = None
        # build embed
        embed = await build_status_embed(countdown)
        try:
            if not message:
                # send new message and save id
                sent = await channel.send(embed=embed)
                cfg['status_message_id'] = sent.id
                save_config(cfg)
                message = sent
            else:
                await message.edit(embed=embed)
        except Exception as e:
            print('Failed to send/edit status message:', e)
            # reset message so we try to resend next cycle
            message = None
        await asyncio.sleep(1)
        countdown -= 1
        if countdown <= 0:
            # time to refresh the data (read from disk next loop) and reset countdown
            refresh = int(cfg.get('refresh_seconds', 60))
            countdown = refresh

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    global status_task_handle
    if status_task_handle is None:
        status_task_handle = bot.loop.create_task(status_loop())

# --- Slash commands ---
from discord import app_commands

@bot.tree.command(name='set_status_refresh', description='Set status refresh interval (owner only)')
@app_commands.describe(interval='Time like 30s, 5m, 2h, 1d')
async def set_status_refresh(interaction: discord.Interaction, interval: str):
    cfg = load_config()
    if interaction.user.id != int(cfg.get('owner_id')):
        await interaction.response.send_message("You don't have permission to change refresh time.", ephemeral=True)
        return
    seconds = parse_time_input(interval)
    if seconds is None:
        await interaction.response.send_message("Invalid time format. Use numbers with s/m/h/d, e.g. 30s or 5m.", ephemeral=True)
        return
    cfg['refresh_seconds'] = int(seconds)
    save_config(cfg)
    await interaction.response.send_message(f"✅ Status refresh time set to {human_readable_seconds(seconds)} ({seconds} seconds).", ephemeral=True)

@bot.tree.command(name='get_status_refresh', description='Get current status refresh interval')
async def get_status_refresh(interaction: discord.Interaction):
    cfg = load_config()
    sec = int(cfg.get('refresh_seconds', 60))
    await interaction.response.send_message(f"Current status refresh: {human_readable_seconds(sec)} ({sec} seconds).", ephemeral=True)

@bot.tree.command(name='set_status_channel', description='Set the channel for the status embed (owner only)')
@app_commands.describe(channel='Text channel for status updates')
async def set_status_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    cfg = load_config()
    if interaction.user.id != int(cfg.get('owner_id')):
        await interaction.response.send_message("You don't have permission to change the status channel.", ephemeral=True)
        return
    cfg['status_channel_id'] = int(channel.id)
    # clear stored message id so bot will post a new one
    cfg['status_message_id'] = None
    save_config(cfg)
    await interaction.response.send_message(f"✅ Status channel set to {channel.mention}", ephemeral=True)

# --- Start Flask in a thread and run bot ---
def run_flask():
    # Flask's built-in server is fine for local/testing. For prod, use gunicorn/uvicorn.
    app.run(host='0.0.0.0', port=5000, threaded=True)

if __name__ == '__main__':
    # start flask in background
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    cfg = load_config()
    token = os.environ.get('DISCORD_TOKEN') or cfg.get('token')
    if not token or token == 'YOUR_DISCORD_BOT_TOKEN_HERE':
        print('Please set your bot token either in', CONFIG_PATH, 'or via the DISCORD_TOKEN environment variable')
    else:
        # allow overriding report secret from env too
        if os.environ.get('REPORT_SECRET'):
            cfg['report_secret'] = os.environ.get('REPORT_SECRET')
            save_config(cfg)
        bot.run(token)
