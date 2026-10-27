import asyncio
import os
import re

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ["BOT_TOKEN"]
CF_API_TOKEN = os.environ["CF_API_TOKEN"]
ZONE_ID = os.environ["CF_ZONE_ID"]
DOMAIN = os.environ["DOMAIN"]
ADMIN_IDS = {int(x) for x in os.environ["ADMIN_IDS"].split(",")}

API = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records"
HEADERS = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

dp = Dispatcher(storage=MemoryStorage())
dp.message.filter(F.from_user.id.in_(ADMIN_IDS))
dp.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


class Add(StatesGroup):
    name = State()
    target = State()
    txt = State()


def kb(rows, width=1):
    b = InlineKeyboardBuilder()
    for text, data in rows:
        b.button(text=text, callback_data=data)
    b.adjust(width)
    return b.as_markup()


MENU = kb([("➕ Subdomain ထည့်မယ်", "add"), ("📋 စာရင်းကြည့်မယ်", "list"), ("🗑 ဖျက်မယ်", "del")])
CANCEL = kb([("❌ ပယ်ဖျက်", "menu")])
BACK = kb([("⬅️ Menu", "menu")])


# ---------- Cloudflare helpers ----------
async def cf(s, method, url=API, **kw):
    async with s.request(method, url, headers=HEADERS, **kw) as r:
        return await r.json()


def err_text(data):
    return data["errors"][0]["message"] if data.get("errors") else "unknown error"


async def upsert(s, rtype, full_name, content):
    """ရှိပြီးသားဆို update၊ မရှိရင် create။ Error မရှိရင် None ပြန်"""
    body = {"type": rtype, "name": full_name, "content": content, "ttl": 1}
    if rtype == "CNAME":
        body["proxied"] = False
    found = await cf(s, "GET", params={"type": rtype, "name": full_name})
    res = found.get("result") or []
    if res:
        data = await cf(s, "PUT", url=f"{API}/{res[0]['id']}", json=body)
    else:
        data = await cf(s, "POST", json=body)
    return None if data.get("success") else err_text(data)


async def get_cnames(s):
    data = await cf(s, "GET", params={"type": "CNAME", "per_page": 100})
    return data.get("result") or []


# ---------- Menu ----------
async def show_menu(target: Message):
    await target.answer(f"🌐 {DOMAIN}\nဘာလုပ်မလဲ?", reply_markup=MENU)


@dp.message(Command("start", "menu"))
async def start(m: Message, state: FSMContext):
    await state.clear()
    await show_menu(m)


@dp.callback_query(F.data == "menu")
async def menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_menu(c.message)
    await c.answer()


# ---------- Add flow ----------
@dp.callback_query(F.data == "add")
async def add_start(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Add.name)
    await c.message.answer("1/3  Subdomain နာမည် ပို့ပါ\nဥပမာ: app1", reply_markup=CANCEL)
    await c.answer()


@dp.message(Add.name)
async def add_name(m: Message, state: FSMContext):
    name = (m.text or "").strip().lower().removesuffix(f".{DOMAIN}")
    if not NAME_RE.match(name):
        return await m.answer("❌ နာမည်မမှန်ပါ။ a-z, 0-9, - ပဲ သုံးပါ။ ပြန်ပို့ပါ", reply_markup=CANCEL)
    await state.update_data(name=name)
    await state.set_state(Add.target)
    await m.answer(
        f"✅ {name}.{DOMAIN}\n\n2/3  Railway ပေးတဲ့ CNAME target ပို့ပါ\nဥပမာ: abc123.up.railway.app",
        reply_markup=CANCEL,
    )


@dp.message(Add.target)
async def add_target(m: Message, state: FSMContext):
    t = (m.text or "").strip().lower()
    t = t.removeprefix("https://").removeprefix("http://").strip("/ .")
    if " " in t or "." not in t:
        return await m.answer("❌ Target မမှန်ပါ။ ဥပမာ abc123.up.railway.app ပြန်ပို့ပါ", reply_markup=CANCEL)
    await state.update_data(target=t)
    await state.set_state(Add.txt)
    await m.answer(
        "3/3  Railway ပေးတဲ့ TXT value ပို့ပါ\nဥပမာ: railway-verify=9053b8...\n\nမလိုရင် Skip နှိပ်ပါ",
        reply_markup=kb([("⏭ TXT မလို (Skip)", "skip"), ("❌ ပယ်ဖျက်", "menu")]),
    )


async def confirm(msg: Message, state: FSMContext, txt):
    await state.update_data(txt=txt)
    await state.set_state(None)  # data ကျန်ရစ်ပြီး state ပိတ်
    d = await state.get_data()
    text = (
        "အတည်ပြုပါ 👇\n\n"
        f"Subdomain: {d['name']}.{DOMAIN}\n"
        f"CNAME → {d['target']}\n"
        f"TXT: {txt or 'မပါ'}"
    )
    await msg.answer(text, reply_markup=kb([("✅ အတည်ပြု", "do_add"), ("❌ ပယ်ဖျက်", "menu")], width=2))


@dp.message(Add.txt)
async def add_txt(m: Message, state: FSMContext):
    txt = (m.text or "").strip()
    if txt and "=" not in txt:
        txt = f"railway-verify={txt}"
    await confirm(m, state, txt or None)


@dp.callback_query(Add.txt, F.data == "skip")
async def add_skip(c: CallbackQuery, state: FSMContext):
    await confirm(c.message, state, None)
    await c.answer()


@dp.callback_query(F.data == "do_add")
async def do_add(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    if not d.get("name"):
        return await c.answer("Session ကုန်သွားပြီ၊ /start ပြန်စပါ", show_alert=True)
    await c.answer("ဖန်တီးနေပါတယ်...")
    full = f"{d['name']}.{DOMAIN}"
    async with aiohttp.ClientSession() as s:
        err = await upsert(s, "CNAME", full, d["target"])
        if err:
            return await c.message.answer(f"❌ CNAME မအောင်ပါ: {err}", reply_markup=BACK)
        out = f"✅ {full} → {d['target']}"
        if d.get("txt"):
            txt_err = await upsert(s, "TXT", f"_railway-verify.{full}", d["txt"])
            out += "\n✅ TXT" if not txt_err else f"\n❌ TXT: {txt_err}"
    out += "\n\nRailway မှာ ✓ ပေါ်တဲ့အထိ စောင့်ပါ"
    await c.message.answer(out, reply_markup=BACK)


# ---------- List ----------
@dp.callback_query(F.data == "list")
async def list_all(c: CallbackQuery):
    await c.answer()
    async with aiohttp.ClientSession() as s:
        recs = await get_cnames(s)
    if not recs:
        return await c.message.answer("မရှိသေးပါ", reply_markup=BACK)
    lines = [f"• {r['name']} → {r['content']}" for r in recs[:40]]
    if len(recs) > 40:
        lines.append(f"... နောက်ထပ် {len(recs) - 40} ခု")
    await c.message.answer("\n".join(lines), reply_markup=BACK)


# ---------- Delete ----------
@dp.callback_query(F.data == "del")
async def del_list(c: CallbackQuery):
    await c.answer()
    async with aiohttp.ClientSession() as s:
        recs = await get_cnames(s)
    if not recs:
        return await c.message.answer("ဖျက်စရာ မရှိပါ", reply_markup=BACK)
    b = InlineKeyboardBuilder()
    for r in recs[:50]:
        label = r["name"].removesuffix(f".{DOMAIN}")
        b.button(text=f"🗑 {label}", callback_data=f"d:{r['id']}")
    b.button(text="⬅️ Menu", callback_data="menu")
    b.adjust(2)
    await c.message.answer("ဖျက်မယ့်ဟာကို ရွေးပါ 👇", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("d:"))
async def del_ask(c: CallbackQuery):
    await c.answer()
    rid = c.data[2:]
    async with aiohttp.ClientSession() as s:
        data = await cf(s, "GET", url=f"{API}/{rid}")
    rec = data.get("result")
    if not rec:
        return await c.message.answer("❌ Record မတွေ့တော့ပါ", reply_markup=BACK)
    await c.message.answer(
        f"🗑 {rec['name']} ကို ဖျက်မလား?\n(TXT verify record ပါ ပါမယ်)",
        reply_markup=kb([("✅ ဖျက်မယ်", f"dc:{rid}"), ("❌ မဖျက်ဘူး", "menu")], width=2),
    )


@dp.callback_query(F.data.startswith("dc:"))
async def del_do(c: CallbackQuery):
    await c.answer("ဖျက်နေပါတယ်...")
    rid = c.data[3:]
    async with aiohttp.ClientSession() as s:
        data = await cf(s, "GET", url=f"{API}/{rid}")
        rec = data.get("result")
        if not rec:
            return await c.message.answer("❌ Record မတွေ့တော့ပါ", reply_markup=BACK)
        await cf(s, "DELETE", url=f"{API}/{rid}")
        found = await cf(s, "GET", params={"type": "TXT", "name": f"_railway-verify.{rec['name']}"})
        for t in found.get("result") or []:
            await cf(s, "DELETE", url=f"{API}/{t['id']}")
    await c.message.answer(
        f"🗑 {rec['name']} ဖျက်ပြီးပါပြီ\n(Railway ဘက်က domain ကိုတော့ ကိုယ်တိုင် ဖျက်ပါ)",
        reply_markup=BACK,
    )


async def main():
    await dp.start_polling(Bot(BOT_TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
