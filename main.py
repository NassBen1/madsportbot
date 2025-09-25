# main.py — Stock par coloris+variante+taille (depuis l’onglet Stock)
# - Affiche stock total par variante
# - Boutons de tailles avec (stock). Si 0 => bouton inactif (callback neutre)
import os, asyncio, time, urllib.parse
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

from sheets import (
    list_clubs, list_products, get_product,
    get_image_for, get_price_for, get_variants_for,
    get_sizes_for, get_stock_for, get_stock_sizes, sum_stock_for,
    append_order
)
from models import carts, add_to_cart, remove_from_cart, empty_cart, cart_total_cents

# ------------------ Config ------------------
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN manquant")

def _parse_admins(s: str|None):
    out=[]
    for x in (s or "").split(","):
        x=x.strip()
        if not x: continue
        try: out.append(int(x))
        except: pass
    return out

ADMINS = _parse_admins(os.getenv("ADMINS",""))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME","").lstrip("@").strip()

PAYPAL_ME = os.getenv("PAYPAL_ME","").strip().replace("https://paypal.me/","").replace("paypal.me/","").lstrip("/")
def paypal_link(order_id:int, total:int):
    if not PAYPAL_ME: return None
    return f"https://www.paypal.me/{PAYPAL_ME}/{total/100:.2f}"

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

checkout = {}  # uid -> {"_active":True, "_stage": ...}

def money(c): return f"{int(c)/100:.2f} €"
def support_url():
    if ADMIN_USERNAME: return f"https://t.me/{ADMIN_USERNAME}"
    for a in ADMINS:
        if a>0: return f"tg://user?id={a}"
    return None

def kb_support_row():
    url=support_url()
    if url: return [InlineKeyboardButton(text="🆘 Aide", url=url)]
    return [InlineKeyboardButton(text="🆘 Aide", callback_data="help")]

async def safe_edit(ev, text, reply_markup=None, parse_mode="Markdown"):
    if isinstance(ev, Message):
        await ev.answer(text, parse_mode=parse_mode, reply_markup=reply_markup); return
    m=ev.message
    try:
        if m.content_type in ("photo","video","animation","document"):
            await m.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await m.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except TelegramBadRequest:
        await m.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)

# ------------------ Helpers ------------------
def _colors(p): return p.get("colors") or []
def _color_index(p, color):
    try: return _colors(p).index(color)
    except ValueError: return -1
def _color_by_index(p, ci):
    cols = _colors(p)
    return cols[ci] if 0 <= ci < len(cols) else None

def _variants_for_color(p, color):
    return get_variants_for(p, color) or []

def _variant_index(variants, name):
    try: return variants.index(name)
    except ValueError: return -1

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def order_summary_text(uid: int) -> str:
    items = carts[uid]
    if not items:
        return "Panier vide."
    lines = ["🧾 *Récapitulatif de ta commande*"]
    for i, it in enumerate(items, start=1):
        qty = int(it.get("qty",1)); price = int(it.get("price_cents",0))*qty
        color = it.get("color") or "—"
        variant = it.get("variant") or "—"
        lines.append(f"{i}. {it['club']} • {color} • {variant} • T.{it['size']} x{qty} — {money(price)}")
    lines.append(f"\nTotal: *{money(cart_total_cents(uid))}*")
    lines.append("\nConfirme pour passer à l'étape *Nom* ou reviens modifier ton panier.")
    return "\n".join(lines)

def min_price_for_product(p: dict) -> int:
    prices = [int(p.get("price_cents",0))]
    for sub in (p.get("color_variant_price_map") or {}).values():
        for v in sub.values():
            try: prices.append(int(v))
            except: pass
    return min(prices) if prices else 0

# ------------------ Commands ------------------
@dp.message(CommandStart())
async def start(m: Message):
    if m.from_user.id in ADMINS:
        await m.answer("✅ Admin reconnu. Vous recevrez les notifications.")
    await m.answer(
        "🏟️ *Bienvenue !* Parcours : Club → Coloris → *Validation* → Variante (stock/price) → *Taille (stock)* → Panier → *Récap* → Paiement.\n"
        "• /catalogue — Clubs\n• /panier — Voir le panier\n• /commander — Finaliser",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Ouvrir le catalogue", callback_data="clubs")],
            [InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")],
            kb_support_row()
        ])
    )

@dp.message(Command("catalogue"))
async def cmd_catalog(m: Message):
    await m.answer("Choisis ton *club* :", parse_mode="Markdown", reply_markup=clubs_kb())

def clubs_kb():
    rows=[[InlineKeyboardButton(text=c, callback_data=f"club:{urllib.parse.quote(c)}")] for c in list_clubs()]
    rows.append([InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")])
    rows.append(kb_support_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(Command("panier"))
async def cmd_cart(m: Message):
    await cart_view(m)

@dp.message(Command("commander"))
async def cmd_order(m: Message):
    await start_checkout(m.from_user.id, m)

@dp.callback_query(F.data=="help")
async def cb_help(cb: CallbackQuery):
    url=support_url()
    if url:
        await cb.message.answer("Besoin d’aide ? Ouvre la conversation :", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Contacter", url=url)]]))
    else:
        await cb.message.answer("Besoin d’aide ? Utilise /help")

# ------------------ Catalogue ------------------
@dp.callback_query(F.data=="clubs")
async def back_clubs(cb: CallbackQuery):
    await cb.message.answer("Choisis ton *club* :", parse_mode="Markdown", reply_markup=clubs_kb())

@dp.callback_query(F.data.startswith("club:"))
async def pick_club(cb: CallbackQuery):
    club = urllib.parse.unquote(cb.data.split(":",1)[1])
    prods = list_products(club=club)
    if not prods:
        await safe_edit(cb, "Aucun maillot trouvé pour ce club.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")], kb_support_row()]));
        return
    p = prods[0]
    colors = _colors(p)
    minp = min_price_for_product(p)
    price_line = f"Prix: {money(minp)}" if minp == int(p.get("price_cents",0)) else f"Prix: à partir de {money(minp)}"
    caption = (
        f"*{p['name']}* — {p['club']}\n"
        f"{price_line}\n"
        f"Coloris: {', '.join(colors) if colors else '—'}"
    )
    rows = [[InlineKeyboardButton(text=c, callback_data=f"color:{p['id']}:{urllib.parse.quote(c)}")] for c in colors] \
           or [[InlineKeyboardButton(text="Passer (pas de coloris)", callback_data=f"color:{p['id']}:")]]
    rows.append([InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")])
    rows.append([InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")])
    rows.append(kb_support_row())
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    img = get_image_for(p, None, None)
    if img:
        try:
            await cb.message.edit_media(InputMediaPhoto(media=img, caption=caption, parse_mode="Markdown")); await cb.message.edit_reply_markup(reply_markup=kb)
        except TelegramBadRequest:
            await safe_edit(cb, caption, kb)
    else:
        await safe_edit(cb, caption, kb)

# ---------- Étape Coloris -> Validation ----------
@dp.callback_query(F.data.startswith("color:"))
async def pick_color(cb: CallbackQuery):
    _, pid_str, color_enc = cb.data.split(":")
    pid = int(pid_str); color = urllib.parse.unquote(color_enc) if color_enc else None
    p = get_product(pid)
    if not p:
        await cb.answer("Indisponible", show_alert=True); return

    if not color:
        await cb.answer("Choisis d’abord un coloris.", show_alert=True)
        return

    caption = (
        f"*{p['name']}* ({p['club']})\n"
        f"Coloris sélectionné: *{color}*\n\n"
        f"✔️ Valide le coloris ou change avant de continuer."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Valider ce coloris", callback_data=f"color_ok:{pid}:{urllib.parse.quote(color)}")],
        [InlineKeyboardButton(text="🔁 Changer de coloris", callback_data=f"color_change:{pid}")],
        [InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")],
        [InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")],
        kb_support_row()
    ])
    img = get_image_for(p, color=color, variant=None)
    try:
        if img: await cb.message.edit_media(InputMediaPhoto(media=img, caption=caption, parse_mode="Markdown")); await cb.message.edit_reply_markup(reply_markup=kb)
        else:   await safe_edit(cb, caption, kb)
    except TelegramBadRequest:
        await safe_edit(cb, caption, kb)

@dp.callback_query(F.data.startswith("color_change:"))
async def color_change(cb: CallbackQuery):
    _, pid_str = cb.data.split(":")
    pid = int(pid_str)
    p = get_product(pid)
    if not p:
        await cb.answer("Indisponible", show_alert=True); return
    colors = _colors(p)
    caption = f"*{p['name']}* — {p['club']}\nSélectionne un *coloris* :"
    rows = [[InlineKeyboardButton(text=c, callback_data=f"color:{p['id']}:{urllib.parse.quote(c)}")] for c in colors] \
           or [[InlineKeyboardButton(text="Passer (pas de coloris)", callback_data=f"color:{p['id']}:")]]
    rows.append([InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")])
    rows.append([InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")])
    rows.append(kb_support_row())
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    img = get_image_for(p, None, None)
    try:
        if img: await cb.message.edit_media(InputMediaPhoto(media=img, caption=caption, parse_mode="Markdown")); await cb.message.edit_reply_markup(reply_markup=kb)
        else:   await safe_edit(cb, caption, kb)
    except TelegramBadRequest:
        await safe_edit(cb, caption, kb)

@dp.callback_query(F.data.startswith("color_ok:"))
async def color_ok(cb: CallbackQuery):
    _, pid_str, color_enc = cb.data.split(":")
    pid = int(pid_str); color = urllib.parse.unquote(color_enc)
    p = get_product(pid)
    if not p:
        await cb.answer("Indisponible", show_alert=True); return

    variants = _variants_for_color(p, color)
    if variants:
        await ask_variant(cb, p, color=color)
        return
    await ask_size(cb, p, color=color, variant=None)

# ---------- Étape Variante (affiche stock total) ----------
def _variant_total_stock(p, color, variant):
    try:
        return int(sum_stock_for(p, color, variant))
    except Exception:
        return 0

async def ask_variant(cb: CallbackQuery, p: dict, color: str):
    variants = _variants_for_color(p, color)
    caption = (
        f"*{p['name']}* — {p['club']}\n"
        f"Coloris: {color}\n"
        f"Choisis une *variante* :"
    )
    ci = _color_index(p, color)
    rows = []
    for vi, v in enumerate(variants):
        price = get_price_for(p, v, color)
        tot  = _variant_total_stock(p, color, v)
        label = f"{v} — {money(price)} • Stock: {tot}"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=f"variant_i:{p['id']}:{ci}:{vi}"
        )])
    rows.append([InlineKeyboardButton(text="🔁 Changer de coloris", callback_data=f"color_change:{p['id']}")])
    rows.append([InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")])
    rows.append([InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")])
    rows.append(kb_support_row())
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    img = get_image_for(p, color=color, variant=None)
    try:
        if img: await cb.message.edit_media(InputMediaPhoto(media=img, caption=caption, parse_mode="Markdown")); await cb.message.edit_reply_markup(reply_markup=kb)
        else:   await safe_edit(cb, caption, kb)
    except TelegramBadRequest:
        await safe_edit(cb, caption, kb)

@dp.callback_query(F.data.startswith("variant_i:"))
async def variant_pick(cb: CallbackQuery):
    _, pid_str, ci_str, vi_str = cb.data.split(":")
    pid = int(pid_str); ci = int(ci_str); vi = int(vi_str)
    p = get_product(pid)
    if not p:
        await cb.answer("Indisponible", show_alert=True); return
    color = _color_by_index(p, ci)
    variants = _variants_for_color(p, color)
    if not color or not (0 <= vi < len(variants)):
        await cb.answer("Option expirée. Reviens au coloris.", show_alert=True); return
    variant = variants[vi]
    price = get_price_for(p, variant, color)
    tot   = _variant_total_stock(p, color, variant)

    caption = (
        f"*{p['name']}* ({p['club']})\n"
        f"Coloris: {color}\n"
        f"Variante sélectionnée: *{variant}*\n"
        f"Prix: *{money(price)}* • Stock total: *{tot}*\n\n"
        f"✔️ Valide la variante ou change."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Valider cette variante", callback_data=f"variant_ok_i:{p['id']}:{ci}:{vi}")],
        [InlineKeyboardButton(text="🔁 Changer de variante", callback_data=f"variant_change_i:{p['id']}:{ci}")],
        [InlineKeyboardButton(text="🔁 Changer de coloris", callback_data=f"color_change:{p['id']}")],
        [InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")],
        [InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")],
        kb_support_row()
    ])
    img = get_image_for(p, color=color, variant=variant)
    try:
        if img: await cb.message.edit_media(InputMediaPhoto(media=img, caption=caption, parse_mode="Markdown")); await cb.message.edit_reply_markup(reply_markup=kb)
        else:   await safe_edit(cb, caption, kb)
    except TelegramBadRequest:
        await safe_edit(cb, caption, kb)

@dp.callback_query(F.data.startswith("variant_change_i:"))
async def variant_change(cb: CallbackQuery):
    _, pid_str, ci_str = cb.data.split(":")
    pid = int(pid_str); ci = int(ci_str)
    p = get_product(pid)
    if not p:
        await cb.answer("Indisponible", show_alert=True); return
    color = _color_by_index(p, ci)
    if not color:
        await cb.answer("Option expirée. Reviens au coloris.", show_alert=True); return
    await ask_variant(cb, p, color=color)

@dp.callback_query(F.data.startswith("variant_ok_i:"))
async def variant_ok(cb: CallbackQuery):
    _, pid_str, ci_str, vi_str = cb.data.split(":")
    pid = int(pid_str); ci = int(ci_str); vi = int(vi_str)
    p = get_product(pid)
    if not p:
        await cb.answer("Indisponible", show_alert=True); return
    color = _color_by_index(p, ci)
    variants = _variants_for_color(p, color)
    if not color or not (0 <= vi < len(variants)):
        await cb.answer("Option expirée. Reviens au coloris.", show_alert=True); return
    variant = variants[vi]
    await ask_size(cb, p, color=color, variant=variant, vi=vi)

# ---------- Étape TAILLE (boutons avec stock ; 0 => inactif) ----------
def _sizes_for(p, color, variant):
    return get_sizes_for(p, color=color, variant=variant) or []

async def ask_size(cb: CallbackQuery, p: dict, color: str, variant: str|None, vi: int|None=None):
    sizes = _sizes_for(p, color, variant)
    stock = get_stock_for(p, color, variant) if variant else {}
    if not sizes:
        await cb.message.answer("Ce couple coloris/variante n'a pas de tailles configurées dans *Stock*.", parse_mode="Markdown")
        return
    caption = (
        f"*{p['name']}* — {p['club']}\n"
        f"Coloris: {color}\n"
        f"{'Variante: ' + variant if variant else ''}\n"
        f"Sélectionne une *taille* :"
    ).strip()

    rows=[]
    ci = _color_index(p, color)
    if vi is None and variant is not None:
        vi = _variant_index(_variants_for_color(p, color), variant)
    if vi is None: vi = -1  # pas de variante

    # 3 colonnes max
    chunked = list(_chunk(list(enumerate(sizes)), 3))
    for row in chunked:
        btns = []
        for si, s in row:
            q = int(stock.get(s, 0))
            label = f"{s} ({q})" if q > 0 else f"{s} (0)"
            if q > 0:
                cbdata = f"size_ok_i:{p['id']}:{ci}:{vi}:{si}"
            else:
                cbdata = f"size_na_i:{p['id']}:{ci}:{vi}:{si}"
            btns.append(InlineKeyboardButton(text=label, callback_data=cbdata))
        rows.append(btns)

    if vi >= 0:
        rows.append([InlineKeyboardButton(text="🔁 Changer de variante", callback_data=f"variant_change_i:{p['id']}:{ci}")])
    rows.append([InlineKeyboardButton(text="🔁 Changer de coloris", callback_data=f"color_change:{p['id']}")])
    rows.append([InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")])
    rows.append([InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")])
    rows.append(kb_support_row())
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    img = get_image_for(p, color=color, variant=variant)
    try:
        if img: await cb.message.edit_media(InputMediaPhoto(media=img, caption=caption, parse_mode="Markdown")); await cb.message.edit_reply_markup(reply_markup=kb)
        else:   await safe_edit(cb, caption, kb)
    except TelegramBadRequest:
        await safe_edit(cb, caption, kb)

@dp.callback_query(F.data.startswith("size_na_i:"))
async def size_na(cb: CallbackQuery):
    await cb.answer("Cette taille est en rupture (0).", show_alert=True)

@dp.callback_query(F.data.startswith("size_ok_i:"))
async def size_ok(cb: CallbackQuery):
    _, pid_str, ci_str, vi_str, si_str = cb.data.split(":")
    pid = int(pid_str); ci = int(ci_str); vi = int(vi_str); si = int(si_str)
    p = get_product(pid)
    if not p:
        await cb.answer("Indisponible", show_alert=True); return

    color = _color_by_index(p, ci)
    variants = _variants_for_color(p, color)
    variant = variants[vi] if vi >= 0 and vi < len(variants) else None
    sizes = _sizes_for(p, color, variant)
    if not color or not (0 <= si < len(sizes)):
        await cb.answer("Option expirée. Reviens au coloris.", show_alert=True); return
    size = sizes[si]

    # Vérification stock temps réel
    q = int(get_stock_for(p, color, variant).get(size, 0)) if variant else 0
    if q <= 0:
        await cb.answer("Cette taille vient de passer à 0. Choisis-en une autre.", show_alert=True)
        return

    price_cents = get_price_for(p, variant, color)
    item = {
        "id": p["id"], "name": p["name"], "club": p["club"],
        "color": color, "variant": variant, "size": size,
        "qty": 1, "price_cents": int(price_cents)
    }
    add_to_cart(cb.from_user.id, item)

    await cb.message.answer(f"✅ Ajouté: {item['club']} • {item['color']} • {item['variant'] or '—'} • T.{item['size']} — {money(item['price_cents'])}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Continuer", callback_data="clubs"), InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")],
        [InlineKeyboardButton(text="✅ Commander", callback_data="checkout:start")],
        kb_support_row()
    ])
    await cb.message.answer("Que souhaites-tu faire ?", reply_markup=kb)

# ------------------ Panier / Checkout identiques ------------------
def clubs_kb():
    rows=[[InlineKeyboardButton(text=c, callback_data=f"club:{urllib.parse.quote(c)}")] for c in list_clubs()]
    rows.append([InlineKeyboardButton(text="📦 Panier", callback_data="cart:view")])
    rows.append(kb_support_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.callback_query(F.data=="cart:view")
async def cart_view_cb(cb: CallbackQuery):
    await cart_view(cb)

async def cart_view(ev):
    uid = ev.from_user.id
    items = carts[uid]
    if not items:
        await safe_edit(ev, "Panier vide.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Clubs", callback_data="clubs")], kb_support_row()]))
        return
    lines=["🧺 *Panier*"]
    for i, it in enumerate(items, start=1):
        base = f"{i}. {it['club']} • {it.get('color') or '—'} • {it.get('variant') or '—'} • T.{it['size']}"
        qty = int(it.get("qty",1)); price = int(it.get("price_cents",0))*qty
        lines.append(f"{base} x{qty} – {money(price)}")
    lines.append(f"\nTotal: *{money(cart_total_cents(uid))}*")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➖ Retirer 1er", callback_data="cart:rm0"), InlineKeyboardButton(text="🗑 Vider", callback_data="cart:empty")],
        [InlineKeyboardButton(text="➕ Continuer", callback_data="clubs"), InlineKeyboardButton(text="✅ Commander", callback_data="checkout:start")],
        kb_support_row()
    ])
    await safe_edit(ev, "\n".join(lines), kb)

@dp.callback_query(F.data=="cart:rm0")
async def cart_rm0(cb: CallbackQuery):
    remove_from_cart(cb.from_user.id, 0); await cart_view(cb)

@dp.callback_query(F.data=="cart:empty")
async def cart_empty(cb: CallbackQuery):
    empty_cart(cb.from_user.id); await cart_view(cb)

async def start_checkout(uid: int, reply_target: Message):
    if not carts[uid]:
        await reply_target.answer("Ton panier est vide.", reply_markup=clubs_kb()); return
    txt = order_summary_text(uid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirmer la commande", callback_data="checkout:confirm")],
        [InlineKeyboardButton(text="🛒 Modifier le panier", callback_data="cart:view")],
        [InlineKeyboardButton(text="⬅️ Continuer les achats", callback_data="clubs")],
        kb_support_row()
    ])
    await reply_target.answer(txt, parse_mode="Markdown", reply_markup=kb)
    checkout[uid] = {"_active": True, "_stage": "confirm"}

@dp.callback_query(F.data=="checkout:start")
async def chk_start(cb: CallbackQuery):
    await start_checkout(cb.from_user.id, cb.message)

@dp.callback_query(F.data=="checkout:confirm")
async def chk_confirm(cb: CallbackQuery):
    uid = cb.from_user.id
    if not carts[uid]:
        await cb.message.answer("Ton panier est vide.", reply_markup=clubs_kb()); return
    checkout[uid]["_stage"] = "name"
    await cb.message.answer("🧾 *Étape 1/3* — Ton *nom complet* :", parse_mode="Markdown")

@dp.message(F.contact)
async def got_contact(m: Message):
    uid = m.from_user.id
    st = checkout.get(uid,{}).get("_stage")
    if not checkout.get(uid,{}).get("_active"):
        return await start_checkout(uid, m)
    if st != "phone":
        checkout[uid]["_stage"] = "name"
        await m.answer("Je te demanderai ton numéro après le nom 😉\n\nD’abord, ton *nom complet* :", parse_mode="Markdown")
        return
    checkout[uid]["phone"] = m.contact.phone_number
    if not checkout[uid].get("name"):
        checkout[uid]["_stage"]="name"; await m.answer("Ton *nom complet* ?")
    else:
        checkout[uid]["_stage"]="address"; await m.answer("🏠 *Adresse complète* :", parse_mode="Markdown")

@dp.callback_query(F.data=="order:new")
async def order_new(cb: CallbackQuery):
    empty_cart(cb.from_user.id)
    checkout.pop(cb.from_user.id, None)
    await cb.message.answer("🆕 Nouvelle commande — choisis ton *club* :", parse_mode="Markdown", reply_markup=clubs_kb())

async def finalize_order(m: Message, uid: int):
    items = carts[uid]; total = cart_total_cents(uid); oid = int(time.time())
    order = {
        "order_id": oid, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": uid, "name": checkout[uid].get("name",""),
        "phone": checkout[uid].get("phone",""), "address": checkout[uid].get("address",""),
        "items_json": items, "total_cents": total, "status": "new",
    }
    append_order(order)

    head = (f"🆕 Commande #{oid}\n{order['name']} — {order['phone']}\n"
            f"Adresse: {order['address']}\nTotal: {money(total)}")
    for a in ADMINS:
        try: await bot.send_message(a, head)
        except: pass
        for it in items:
            cap = f"{it['club']} • {it.get('color') or '—'} • {it.get('variant') or '—'} • T.{it['size']} x{int(it.get('qty',1))} — {money(int(it.get('price_cents',0))*int(it.get('qty',1)))}"
            try:
                p = get_product(it["id"]); img = get_image_for(p, color=it.get('color'), variant=it.get('variant'))
                if img: await bot.send_photo(a, img, caption=cap)
                else:   await bot.send_message(a, cap)
            except: pass

    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Payer via PayPal (entre proches)", url=paypal_link(oid, total) or "https://www.paypal.me/")],
        [InlineKeyboardButton(text="🛍️ Nouvelle commande", callback_data="order:new")],
        kb_support_row()
    ])
    await m.answer(f"✅ Commande #{oid} enregistrée.\nTotal: *{money(total)}*\n\nClique pour *payer via PayPal.me* et ajoute en note: `Commande #{oid}`.", parse_mode="Markdown", reply_markup=pay_kb)
    empty_cart(uid); checkout.pop(uid, None)

# ------------------ Run ------------------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
