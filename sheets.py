# sheets.py — Products/Orders/Stock
# - Stock par coloris+variante+taille depuis l'onglet "Stock"
# - Le bot n'utilise plus 'sizes' de Products pour l'affichage : l'ordre des tailles vient des en-têtes de Stock
import os, time, json, re
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound

load_dotenv()
SHEET_ID      = os.getenv("SHEET_ID")
PRODUCTS_TAB  = os.getenv("PRODUCTS_TAB", "Products")
ORDERS_TAB    = os.getenv("ORDERS_TAB", "Orders")
STOCK_TAB     = os.getenv("STOCK_TAB",  "Stock")

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_gc = _sh = None

_cache = {
    "products": ([], 0),
    "stock":    ({}, [], 0),  # (stock_map, size_headers, ts)
}
TTL = 5  # s

# --------- helpers ---------
def _normalize_row_keys(r: dict) -> dict:
    def _norm_key(k: str) -> str:
        return re.sub(r"\s+", "_", (k or "").strip().lower())
    return {_norm_key(k): v for k, v in r.items()}

def _parse_list(val: str):
    if not val: return []
    return [s.strip() for s in str(val).split(",") if s.strip()]

# Drive images
_RX_DRIVE_FILE = re.compile(r"https?://drive\.google\.com/file/d/([^/]+)/?")
_RX_DRIVE_OPEN = re.compile(r"https?://drive\.google\.com/open\?id=([^&]+)")
_RX_ID_IN_QS   = re.compile(r"[?&]id=([^&]+)")

def _extract_gdrive_id(v: str|None):
    if not v: return None
    v = str(v).strip()
    m = _RX_DRIVE_FILE.search(v) or _RX_DRIVE_OPEN.search(v) or _RX_ID_IN_QS.search(v)
    if m: return m.group(1)
    if len(v) >= 20 and "/" not in v and " " not in v and "http" not in v:
        return v
    return None

def _drive_direct_image(fid: str) -> str:
    return f"https://lh3.googleusercontent.com/d/{fid}"

def _to_direct(v: str|None) -> str|None:
    if not v: return v
    fid = _extract_gdrive_id(v)
    return _drive_direct_image(fid) if fid else v

def _parse_imgmap(val: str):
    if not val: return {}
    try:
        data = json.loads(val)
        return {str(k).strip(): _to_direct(str(v).strip()) for k, v in data.items()}
    except Exception:
        return {}

def _parse_price_value(x):
    if x is None: return None
    if isinstance(x, (int, float)):
        return int(round(float(x) * 100))
    s = str(x).replace("€", "").strip().replace(",", ".")
    try:
        if "." in s: return int(round(float(s) * 100))
        return int(s)
    except Exception:
        return None

def _parse_nested_price_map(val: str):
    """color -> { variant -> price_cents }"""
    if not val: return {}
    try:
        data = json.loads(val)
        out = {}
        for color, sub in data.items():
            if isinstance(sub, dict):
                out[str(color).strip()] = {str(v).strip(): _parse_price_value(p) for v, p in sub.items() if _parse_price_value(p) is not None}
        return out
    except Exception:
        return {}

def _parse_color_variant_list_map(val: str):
    """color -> [variants]"""
    if not val: return {}
    try:
        data = json.loads(val)
        out = {}
        for color, arr in data.items():
            if isinstance(arr, list):
                out[str(color).strip()] = [str(x).strip() for x in arr if str(x).strip()]
        return out
    except Exception:
        return {}

def _ci_get_map(m: dict, key: str|None):
    if not key or not m: return None
    needle = key.strip().lower()
    for k, v in m.items():
        if str(k).strip().lower() == needle:
            return v
    return None

def _ci_get_nested(m: dict, k1: str|None, k2: str|None):
    sub = _ci_get_map(m, k1)
    if isinstance(sub, dict):
        return _ci_get_map(sub, k2)
    return None

# -------- Sheets client --------
def _ensure_client():
    global _gc, _sh
    if _gc is None:
        if not SHEET_ID:
            raise RuntimeError("SHEET_ID manquant dans .env")
        creds = Credentials.from_service_account_file("service_account.json", scopes=_SCOPES)
        _gc = gspread.authorize(creds)
    if _sh is None:
        _sh = _gc.open_by_key(SHEET_ID)

def _ensure_ws(name: str, headers: list[str]|None=None, cols: int=12, init_rows: int=2000):
    _ensure_client()
    try:
        ws = _sh.worksheet(name)
    except WorksheetNotFound:
        ws = _sh.add_worksheet(title=name, rows=init_rows, cols=max(cols, 26))
        if headers:
            ws.update(f"A1:{chr(64+len(headers))}1", [headers])
    return ws

# -------- Products ----------
def get_products(force: bool=False):
    now = time.time()
    if not force and now - _cache["products"][1] < TTL:
        return _cache["products"][0]

    _ensure_client()
    ws = _sh.worksheet(PRODUCTS_TAB)
    rows = ws.get_all_records()
    out = []
    for raw in rows:
        r = _normalize_row_keys(raw)
        active = str(r.get("active", 1)).lower() in ("1","true","vrai","yes","oui")
        if not active: continue
        try:
            out.append({
                "id": int(r.get("id")),
                "name": str(r.get("name","")).strip(),
                "club": str(r.get("club","")).strip(),
                "season": str(r.get("season","")).strip(),
                "price_cents": int(_parse_price_value(r.get("price_cents")) or 0),
                # 'sizes' n'est plus utilisé pour l'affichage, mais on le laisse si besoin
                "sizes": str(r.get("sizes","")).strip(),
                "colors": _parse_list(r.get("colors","")),
                "image": _to_direct(str(r.get("image_url","")).strip()),
                "image_color_map": _parse_imgmap(r.get("image_color_map_json","")),
                "image_color_variant_map": _parse_nested_price_map(None),  # placeholder pour compat
                "color_variant_map": _parse_color_variant_list_map(r.get("color_variant_map_json","")),
                "color_variant_price_map": _parse_nested_price_map(r.get("color_variant_price_map_json","")),
                "image_color_variant_map": _parse_imgmap(r.get("image_color_variant_map_json","")) if r.get("image_color_variant_map_json") else {},
                "stock": int(r.get("stock",0) or 0),
            })
        except Exception:
            continue
    _cache["products"] = (out, now)
    return out

def list_clubs():
    return sorted({p["club"] for p in get_products() if p.get("club")})

def list_products(club=None, season=None):
    prods = get_products()
    if club: prods = [p for p in prods if p["club"] == club]
    if season: prods = [p for p in prods if p["season"] == season]
    return prods

def get_product(pid: int):
    for p in get_products():
        if p["id"] == pid: return p
    return None

# ---------- VARIANTS / PRICE / IMAGE ----------
def get_variants_for(product: dict, color: str|None):
    if not color:
        return []
    v = _ci_get_map(product.get("color_variant_map") or {}, color)
    return v if isinstance(v, list) else []

def get_price_for(product: dict, variant: str|None = None, color: str|None = None) -> int:
    if variant and color:
        v = _ci_get_nested(product.get("color_variant_price_map") or {}, color, variant)
        if isinstance(v, (int, float)): return int(v)
    return int(product.get("price_cents", 0))

def get_image_for(product: dict, color: str|None = None, variant: str|None = None):
    # 1) color+variant (si fourni en map d'images imbriquée)
    m = product.get("image_color_variant_map") or {}
    sub = _ci_get_map(m, color) if color else None
    if isinstance(sub, dict):
        img = _ci_get_map(sub, variant)
        if img: return img
    # 2) color seul
    img = _ci_get_map(product.get("image_color_map") or {}, color)
    if img: return img
    # 3) générique
    return product.get("image") or ""

# ---------- STOCK ----------
def _norm(s):
    return (s or "").strip().lower()

def _load_stock(force: bool=False):
    now = time.time()
    stock_map, size_headers, ts = _cache["stock"]
    if not force and now - ts < TTL and size_headers:
        return stock_map, size_headers

    _ensure_client()
    ws = _sh.worksheet(STOCK_TAB)
    headers_orig = ws.row_values(1)
    headers_norm = [_norm(h) for h in headers_orig]

    # repérage des colonnes clés
    def find_col(name):
        name = _norm(name)
        try: return headers_norm.index(name)
        except ValueError: return -1

    id_idx    = find_col("id")
    color_idx = find_col("colors")
    var_idx   = find_col("color_variant")
    if id_idx < 0 or color_idx < 0 or var_idx < 0:
        raise RuntimeError("L'onglet Stock doit contenir les colonnes: ID, colors, color_variant (+ colonnes de tailles)")

    # tailles = toutes colonnes sauf (id, club, colors, color_variant)
    skip = {id_idx, color_idx, var_idx}
    club_idx = find_col("club")
    if club_idx >= 0: skip.add(club_idx)

    size_headers = [headers_orig[i].strip() for i in range(len(headers_orig)) if i not in skip]

    # lecture des lignes
    rows = ws.get_all_records()
    stock_map = {}  # (pid, color_norm, variant_norm) -> { size_label -> qty_int }
    for r in rows:
        try:
            pid = int(r.get(headers_orig[id_idx]))
        except Exception:
            continue
        color = str(r.get(headers_orig[color_idx]) or "").strip()
        variant = str(r.get(headers_orig[var_idx]) or "").strip()
        if not color or not variant:
            continue

        key = (pid, _norm(color), _norm(variant))
        sizes = stock_map.get(key, {})
        for sh in size_headers:
            val = r.get(sh, 0)
            try:
                q = int(val)
            except Exception:
                q = 0
            sizes[sh.strip()] = sizes.get(sh.strip(), 0) + q  # si doublon de ligne, on additionne
        stock_map[key] = sizes

    _cache["stock"] = (stock_map, size_headers, now)
    return stock_map, size_headers

def get_stock_sizes():
    _, size_headers = _load_stock()
    return size_headers

def get_stock_for(product: dict, color: str|None, variant: str|None):
    if not product or not color or not variant:
        return {}
    stock_map, size_headers = _load_stock()
    key = (int(product["id"]), _norm(color), _norm(variant))
    sizes = stock_map.get(key, {})
    # normalise: garantir toutes les tailles connues
    return {sh: int(sizes.get(sh, 0)) for sh in size_headers}

def sum_stock_for(product: dict, color: str|None, variant: str|None) -> int:
    d = get_stock_for(product, color, variant)
    return sum(int(v) for v in d.values())

# ---------- (compat) tailles pour UI : ordre = entêtes de Stock ----------
def get_sizes_for(product: dict, color: str|None = None, variant: str|None = None):
    return get_stock_sizes()

# -------- Orders ----------
def _first_empty_row(ws, start_row: int = 2, key_col: str = "A", chunk: int = 500) -> int:
    row = start_row
    while True:
        end = min(ws.row_count, row + chunk - 1)
        if end < row: return ws.row_count + 1
        rng = f"{key_col}{row}:{key_col}{end}"
        values = ws.get(rng, value_render_option="UNFORMATTED_VALUE", major_dimension="COLUMNS")
        col = values[0] if values else []
        if len(col) < (end - row + 1):
            col += [None] * ((end - row + 1) - len(col))
        for i, val in enumerate(col, start=row):
            if val is None or str(val).strip() == "":
                return i
        if end >= ws.row_count:
            return ws.row_count + 1
        row = end + 1

def _ensure_row_capacity(ws, row_idx: int, buffer: int = 100):
    if row_idx > ws.row_count:
        ws.add_rows(row_idx - ws.row_count + buffer)

def append_order(order: dict):
    headers = ["order_id","timestamp","user_id","name","phone","address","items_json","total_cents","status"]
    ws = _ensure_ws(ORDERS_TAB, headers=headers, cols=len(headers)+2, init_rows=2000)

    row_idx = _first_empty_row(ws, start_row=2, key_col="A", chunk=500)
    _ensure_row_capacity(ws, row_idx, buffer=100)

    import json as _json
    row = [
        order.get("order_id",""),
        order.get("timestamp",""),
        order.get("user_id",""),
        order.get("name",""),
        order.get("phone",""),
        order.get("address",""),
        _json.dumps(order.get("items_json",[]), ensure_ascii=False),
        int(order.get("total_cents",0)),
        order.get("status","new"),
    ]
    ws.update(f"A{row_idx}:I{row_idx}", [row], value_input_option="USER_ENTERED")
