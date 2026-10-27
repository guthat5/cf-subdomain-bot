import asyncio
import os
import re

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

BOT_TOKEN = os.environ["BOT_TOKEN"]
CF_API_TOKEN = os.environ["CF_API_TOKEN"]
ZONE_ID = os.environ["CF_ZONE_ID"]
DOMAIN = os.environ["DOMAIN"]  # ဥပမာ example.com
ADMIN_IDS = {int(x) for x in os.environ["ADMIN_IDS"].split(",")}

API = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records"
HEADERS = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

dp = Dispatcher()
dp.message.filter(F.from_user.id.in_(ADMIN_IDS))

HELP = (
    "Railway မှာ domain အရင်ထည့်ပြီး ပေးတဲ့ တန်ဖိုးတွေကို ဒီလိုပို့ပါ\n\n"
    "/add name target [txt_value]\n"
    "ဥပမာ:\n/add app1 abc123.up.railway.app railway-verify=xyz\n\n"
    "တစ်ကြောင်းချင်း အများကြီးလည်း ရပါတယ် (command တစ်ခုတည်းအောက်မှာ line ခွဲ)\n"
    "/del name1 name2\n/list"
)


async def cf(s, method, url=API, **kw):
    async with s.request(method, url, headers=HEADERS, **kw) as r:
        return await r.json()


async def upsert(s, rtype, full_name, content):
    """ရှိပြီးသား record ရှိရင် update၊ မရှိရင် create"""
    body = {"type": rtype, "name": full_name, "content": content, "ttl": 1}
    if rtype == "CNAME":
        body["proxied"] = False  # Railway verify မပြီးခင် DNS only
    found = await cf(s, "GET", params={"type": rtype, "name": full_name})
    res = found.get("result") or []
    if res:
        data = await cf(s, "PUT", url=f"{API}/{res[0]['id']}", json=body)
    else:
        data = await cf(s, "POST", json=body)
    if data.get("success"):
        return None
    return data["errors"][0]["message"] if data.get("errors") else "unknown error"


@dp.message(Command("start", "help"))
async def start(m: Message):
    await m.answer(HELP)


@dp.message(Command("add"))
async def add(m: Message, command: CommandObject):
    lines = [l.strip() for l in (command.args or "").splitlines() if l.strip()]
    if not lines:
        return await m.answer(HELP)
    out = []
    async with aiohttp.ClientSession() as s:
        for line in lines[:50]:
            parts = line.split()
            name = parts[0].lower().removesuffix(f".{DOMAIN}")
            if len(parts) < 2 or not NAME_RE.match(name):
                out.append(f"❌ {line} (format မှား)")
                continue
            target = parts[1].rstrip(".")
            err = await upsert(s, "CNAME", f"{name}.{DOMAIN}", target)
            if err:
                out.append(f"❌ {name} CNAME: {err}")
                continue
            msg = f"✅ {name}.{DOMAIN} → {target}"
            if len(parts) >= 3:
                txt_err = await upsert(s, "TXT", f"_railway-verify.{name}.{DOMAIN}", parts[2])
                msg += "\n   TXT ✅" if not txt_err else f"\n   TXT ❌ {txt_err}"
            out.append(msg)
            await asyncio.sleep(0.3)
    await m.answer("\n".join(out))


@dp.message(Command("del"))
async def delete(m: Message, command: CommandObject):
    names = (command.args or "").lower().split()[:50]
    if not names:
        return await m.answer("/del name1 name2")
    out = []
    async with aiohttp.ClientSession() as s:
        for n in names:
            n = n.removesuffix(f".{DOMAIN}")
            hit = False
            for full, rtype in ((f"{n}.{DOMAIN}", "CNAME"), (f"_railway-verify.{n}.{DOMAIN}", "TXT")):
                found = await cf(s, "GET", params={"type": rtype, "name": full})
                for rec in found.get("result") or []:
                    await cf(s, "DELETE", url=f"{API}/{rec['id']}")
                    hit = True
            out.append(f"🗑 {n}" if hit else f"❌ {n} (မတွေ့)")
    await m.answer("\n".join(out))


@dp.message(Command("list"))
async def list_records(m: Message):
    async with aiohttp.ClientSession() as s:
        data = await cf(s, "GET", params={"type": "CNAME", "per_page": 100})
    rows = [f"{r['name']} → {r['content']}" for r in data.get("result", [])]
    await m.answer("\n".join(rows) if rows else "မရှိသေးပါ")


async def main():
    await dp.start_polling(Bot(BOT_TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
