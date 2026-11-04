import os
import io
import json
import uuid
import base64
import urllib.request
from datetime import datetime
from PIL import Image
from fpdf import FPDF

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from telegram import Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove

# ================= CONFIGURATION =================
BOT_TOKEN = "9053956555:AAF2pUW5OkLbsMSVJLnMOJuxIEg4NWYE_TE"
ADMIN_USER = "Junide"
ADMIN_PASS = "forevertogether2006"

# အဆင့် (၁) မှ ရရှိသော Firebase Realtime Database URL ကို ဒီနေရာတွင် ထည့်ပါ
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "https://tiktokonlineshop-8e680-default-rtdb.firebaseio.com/")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_FILE = os.path.join(BASE_DIR, "database.json")

# ================= INITIAL DEFAULT DATA =================
DEFAULT_DB = {
    "admin_chat_ids": [5797724379],
    "categories": [
        {"id": "all", "name": "General"},
        {"id": "a7c62c", "name": "ရေမွှေးများ"}
    ],
    "products": [
        {
            "id": "3f67374c",
            "image": "/static/uploads/prod_08c9bd12.png",
            "bg_color": "rgba(196, 183, 161, 0.22)",
            "glow_color": "rgba(196, 183, 161, 0.45)",
            "out_of_stock": False,
            "name": "Me Perfume",
            "info": "Natural Spray Perfume (100 ml)",
            "category": "ရေမွှေးများ",
            "price": 20000
        }
    ],
    "shipping_options": [
        {"id": "royal", "name": "Royal", "requires_gate": False},
        {"id": "kargate", "name": "ကားဂိတ်", "requires_gate": True}
    ],
    "payment_methods": [
        {
            "id": "kbzpay",
            "name": "KBZ Pay",
            "logo": "/static/kpay.jpg",
            "qr": "/static/qr.jpg",
            "phone": "09664994195",
            "account_name": "Thura"
        }
    ],
    "bot_sessions": {}
}

# ================= CLOUD DATABASE FUNCTIONS =================
def _normalize_db(data: dict) -> dict:
    for key in ["admin_chat_ids", "categories", "products", "shipping_options", "payment_methods"]:
        val = data.get(key)
        if isinstance(val, dict):
            data[key] = list(val.values())
        elif not isinstance(val, list):
            data[key] = DEFAULT_DB[key]
    if not isinstance(data.get("bot_sessions"), dict):
        data["bot_sessions"] = {}
    return data

def load_db() -> dict:
    if FIREBASE_DB_URL and "YOUR-PROJECT-ID" not in FIREBASE_DB_URL:
        try:
            url = f"{FIREBASE_DB_URL.rstrip('/')}/shop_db.json"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                if data is None:
                    save_db(DEFAULT_DB)
                    return json.loads(json.dumps(DEFAULT_DB))
                return _normalize_db(data)
        except Exception as e:
            print(f"Firebase load error: {e}")

    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return _normalize_db(json.load(f))
    return json.loads(json.dumps(DEFAULT_DB))

def save_db(data: dict):
    if FIREBASE_DB_URL and "YOUR-PROJECT-ID" not in FIREBASE_DB_URL:
        try:
            url = f"{FIREBASE_DB_URL.rstrip('/')}/shop_db.json"
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, method="PUT",
                headers={"Content-Type": "application/json; charset=utf-8"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return
        except Exception as e:
            print(f"Firebase save error: {e}")

    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass

# ================= IMAGE PROCESSING (BASE64 IN-MEMORY) =================
def process_product_image_base64(image_bytes: bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    img.thumbnail((420, 420))
    datas = img.getdata()
    new_data = []

    for item in datas:
        if item[0] > 235 and item[1] > 235 and item[2] > 235:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)

    img.putdata(new_data)

    small_img = img.copy()
    small_img.thumbnail((50, 50))
    pixels = [p for p in small_img.getdata() if p[3] > 128]

    if pixels:
        r = sum(p[0] for p in pixels) // len(pixels)
        g = sum(p[1] for p in pixels) // len(pixels)
        b = sum(p[2] for p in pixels) // len(pixels)
        bg_color = f"rgba({r}, {g}, {b}, 0.22)"
        glow_color = f"rgba({r}, {g}, {b}, 0.45)"
    else:
        bg_color = "rgba(255, 90, 121, 0.18)"
        glow_color = "rgba(255, 90, 121, 0.35)"

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_str}"
    return data_url, bg_color, glow_color

def process_logo_image_base64(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((200, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64_str}"

# ================= PDF GENERATOR (IN-MEMORY BYTES) =================
def generate_order_pdf_bytes(order_data: dict) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_fill_color(255, 90, 121)
    pdf.rect(0, 0, 210, 35, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 15, "ORDER INVOICE / RECEIPT", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Order ID: #{order_data['order_id']}  |  Date: {order_data['date']}", ln=True, align="C")
    pdf.ln(12)

    pdf.set_text_color(30, 41, 59)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Customer Information", ln=True)
    pdf.set_draw_color(226, 232, 240)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 11)
    info_lines = [
        f"Name: {order_data['name']}",
        f"Phone 1: {order_data['phone1']}",
        f"Phone 2: {order_data['phone2']}",
        f"Address: {order_data['address']}",
        f"Shipping Option: {order_data['shipping']}",
    ]
    if order_data.get("bus_line") or order_data.get("gate_name"):
        info_lines.append(f"Bus Line / Gate: {order_data.get('bus_line', '')} ({order_data.get('gate_name', '')})")
    info_lines.append(f"Payment Method: {order_data['payment_method']}")

    for line in info_lines:
        safe_line = line.encode("latin-1", "replace").decode("latin-1")
        pdf.cell(0, 7, safe_line, ln=True)

    pdf.ln(6)

    pdf.set_fill_color(241, 245, 249)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(95, 10, "Product Name", border=1, fill=True)
    pdf.cell(30, 10, "Qty", border=1, align="C", fill=True)
    pdf.cell(30, 10, "Price", border=1, align="R", fill=True)
    pdf.cell(35, 10, "Subtotal", border=1, align="R", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 11)
    for item in order_data["items"]:
        p_name = str(item["name"]).encode("latin-1", "replace").decode("latin-1")
        qty = int(item["qty"])
        price = int(item["price"])
        sub = qty * price
        pdf.cell(95, 9, f" {p_name}", border=1)
        pdf.cell(30, 9, str(qty), border=1, align="C")
        pdf.cell(30, 9, f"{price:,} Ks", border=1, align="R")
        pdf.cell(35, 9, f"{sub:,} Ks", border=1, align="R")
        pdf.ln()

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(155, 11, "TOTAL PAYMENT ", border=1, align="R", fill=True)
    pdf.set_text_color(255, 90, 121)
    pdf.cell(35, 11, f"{int(order_data['total']):,} Ks", border=1, align="R", fill=True)
    return bytes(pdf.output())

# ================= SERVERLESS TELEGRAM WEBHOOK HANDLER =================
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["1. Product အသစ်တင်ရန်", "2. Category အသစ်တိုးရန်"],
        ["3. Product စီမံရန် (ပြင်/ဖျက်/Stock)"],
        ["4. Shipping Option တိုးရန်", "5. Payment Add လုပ်ရန်"],
    ],
    resize_keyboard=True,
)

async def handle_telegram_update(update: Update, bot: Bot):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    chat_key = str(chat_id)
    text = (update.message.text or "").strip()

    db = load_db()
    session = db["bot_sessions"].get(chat_key, {"state": "ASK_USERNAME", "data": {}})
    state = session.get("state", "ASK_USERNAME")
    s_data = session.get("data", {})

    # /start ရိုက်လိုက်တိုင်း Login အဆင့်မှ ပြန်စမည်
    if text == "/start":
        db["bot_sessions"][chat_key] = {"state": "ASK_USERNAME", "data": {}}
        save_db(db)
        await bot.send_message(
            chat_id=chat_id,
            text="🔐 Master Admin Panel သို့ ဝင်ရောက်ရန် **Username** ရိုက်ထည့်ပေးပါ -",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    if state == "ASK_USERNAME":
        if text == ADMIN_USER:
            db["bot_sessions"][chat_key] = {"state": "ASK_PASSWORD", "data": {}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text="🔑 **Password** ဆက်လက်ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=chat_id, text="❌ Username မှားယွင်းနေပါသည်။ ပြန်လည်ရိုက်ထည့်ပါ -")

    elif state == "ASK_PASSWORD":
        if text == ADMIN_PASS:
            if chat_id not in db["admin_chat_ids"]:
                db["admin_chat_ids"].append(chat_id)
            db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
            save_db(db)
            await bot.send_message(
                chat_id=chat_id,
                text="✅ Login အောင်မြင်ပါပြီ။ လုပ်ဆောင်လိုသည်ကို ရွေးချယ်ပါ -",
                reply_markup=MAIN_KEYBOARD
            )
        else:
            await bot.send_message(chat_id=chat_id, text="❌ Password မှားယွင်းနေပါသည်။ ပြန်လည်ရိုက်ထည့်ပါ -")

    elif state == "MAIN_MENU":
        if text.startswith("1"):
            db["bot_sessions"][chat_key] = {"state": "PROD_PHOTO", "data": {}}
            save_db(db)
            await bot.send_message(
                chat_id=chat_id,
                text="📸 **Product ပုံ ပို့ပေးပါ** (White background) -",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text.startswith("2"):
            db["bot_sessions"][chat_key] = {"state": "ADD_CAT", "data": {}}
            save_db(db)
            await bot.send_message(
                chat_id=chat_id,
                text="📂 အသစ်တိုးမည့် **Category Name** ကို ရိုက်ထည့်ပေးပါ -",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text.startswith("3"):
            if not db["products"]:
                await bot.send_message(chat_id=chat_id, text="❌ စီမံရန် Product မရှိသေးပါ။", reply_markup=MAIN_KEYBOARD)
                return
            msg = "⚙️ **စီမံလိုသော Product ၏ နံပါတ်ကို ရိုက်ထည့်ပါ**\n\n"
            for i, p in enumerate(db["products"]):
                status = "[🔴 Out of Stock]" if p.get("out_of_stock") else "[🟢 In Stock]"
                msg += f"{i+1}. {p['name']} {status}\n"
            msg += "\n(မလုပ်တော့ပါက `cancel` ဟု ရိုက်ထည့်ပါ)"
            db["bot_sessions"][chat_key] = {"state": "MANAGE_PROD_SELECT", "data": {}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        elif text.startswith("4"):
            db["bot_sessions"][chat_key] = {"state": "SHIP_NAME", "data": {}}
            save_db(db)
            await bot.send_message(
                chat_id=chat_id,
                text="🚚 အသစ်တိုးမည့် **Shipping Option Name** ကို ရိုက်ထည့်ပေးပါ -",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text.startswith("5"):
            db["bot_sessions"][chat_key] = {"state": "PAY_NAME", "data": {}}
            save_db(db)
            await bot.send_message(
                chat_id=chat_id,
                text="💳 အသစ်ထည့်မည့် **Payment အမည်** ကို ရိုက်ထည့်ပေးပါ -",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await bot.send_message(chat_id=chat_id, text="မီနူးမှ ရွေးချယ်ပေးပါ -", reply_markup=MAIN_KEYBOARD)

    elif state == "ADD_CAT":
        db["categories"].append({"id": uuid.uuid4().hex[:6], "name": text})
        db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
        save_db(db)
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Category **{text}** ကို ထည့်သွင်းပြီးပါပြီ!",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )

    elif state == "PROD_PHOTO":
        if not update.message.photo:
            await bot.send_message(chat_id=chat_id, text="📸 ကျေးဇူးပြု၍ ပုံပို့ပေးပါ -")
            return
        await bot.send_message(chat_id=chat_id, text="⏳ ပုံကို Auto Background ဖျောက်နေပါသည်...")
        photo_file = await bot.get_file(update.message.photo[-1].file_id)
        img_bytes = await photo_file.download_as_bytearray()
        img_url, bg_color, glow_color = process_product_image_base64(bytes(img_bytes))
        s_data["new_prod"] = {
            "id": uuid.uuid4().hex[:8],
            "image": img_url,
            "bg_color": bg_color,
            "glow_color": glow_color,
            "out_of_stock": False
        }
        db["bot_sessions"][chat_key] = {"state": "PROD_NAME", "data": s_data}
        save_db(db)
        await bot.send_message(chat_id=chat_id, text="🏷 **Product Name** ပေးပါ -", parse_mode="Markdown")

    elif state == "PROD_NAME":
        s_data["new_prod"]["name"] = text
        db["bot_sessions"][chat_key] = {"state": "PROD_INFO", "data": s_data}
        save_db(db)
        await bot.send_message(
            chat_id=chat_id,
            text="📝 **Product Info** (Product Name အောက်တွင်ပေါ်မည့် အချက်အလက်အရှည်) ကို ရိုက်ထည့်ပါ -",
            parse_mode="Markdown"
        )

    elif state == "PROD_INFO":
        s_data["new_prod"]["info"] = text
        db["bot_sessions"][chat_key] = {"state": "PROD_CAT", "data": s_data}
        save_db(db)
        cat_buttons = [[c["name"]] for c in db["categories"]]
        await bot.send_message(
            chat_id=chat_id,
            text="📂 **မည်သည့် Category အောက်တွင် ထားမည်နည်း?**",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(cat_buttons, resize_keyboard=True)
        )

    elif state == "PROD_CAT":
        s_data["new_prod"]["category"] = text
        db["bot_sessions"][chat_key] = {"state": "PROD_PRICE", "data": s_data}
        save_db(db)
        await bot.send_message(
            chat_id=chat_id,
            text="💰 **Price** (စျေးနှုန်း - ဂဏန်းသီးသန့်) ရိုက်ထည့်ပါ -",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )

    elif state == "PROD_PRICE":
        price_str = text.replace(",", "").replace("Ks", "").strip()
        if not price_str.isdigit():
            await bot.send_message(chat_id=chat_id, text="ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ -")
            return
        prod = s_data["new_prod"]
        prod["price"] = int(price_str)
        db["products"].append(prod)
        db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
        save_db(db)
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ **{prod['name']}** ကို Web သို့ တင်ပြီးပါပြီ!",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )

    elif state == "MANAGE_PROD_SELECT":
        if text.lower() == "cancel":
            db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text="❌ ပယ်ဖျက်လိုက်ပါသည်။", reply_markup=MAIN_KEYBOARD)
            return
        if not text.isdigit():
            return
        idx = int(text) - 1
        if 0 <= idx < len(db["products"]):
            p = db["products"][idx]
            status = "🔴 Out of Stock" if p.get("out_of_stock") else "🟢 In Stock"
            msg = f"📦 **{p['name']}**\nInfo: {p.get('info', '-')}\nStatus: {status}\n\nမည်သို့ လုပ်ဆောင်လိုသနည်း?"
            kb = [["1. Name ပြင်မည်", "2. Info ပြင်မည်"], ["3. Stock အခြေအနေပြောင်းမည်"], ["4. အပြီးဖျက်မည်", "Cancel"]]
            db["bot_sessions"][chat_key] = {"state": "MANAGE_PROD_ACTION", "data": {"edit_idx": idx}}
            save_db(db)
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )

    elif state == "MANAGE_PROD_ACTION":
        idx = s_data.get("edit_idx", 0)
        if text.startswith("1"):
            db["bot_sessions"][chat_key] = {"state": "EDIT_PROD_NAME", "data": {"edit_idx": idx}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text="🏷 Product Name အသစ် ရိုက်ထည့်ပါ -", reply_markup=ReplyKeyboardRemove())
        elif text.startswith("2"):
            db["bot_sessions"][chat_key] = {"state": "EDIT_PROD_INFO", "data": {"edit_idx": idx}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text="📝 Product Info အသစ် ရိုက်ထည့်ပါ -", reply_markup=ReplyKeyboardRemove())
        elif text.startswith("3"):
            db["products"][idx]["out_of_stock"] = not db["products"][idx].get("out_of_stock", False)
            db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text="✅ Stock အခြေအနေ ပြောင်းလဲပြီးပါပြီ။", reply_markup=MAIN_KEYBOARD)
        elif text.startswith("4"):
            deleted = db["products"].pop(idx)
            db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text=f"✅ {deleted['name']} ကို အပြီးဖျက်လိုက်ပါပြီ။", reply_markup=MAIN_KEYBOARD)
        else:
            db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
            save_db(db)
            await bot.send_message(chat_id=chat_id, text="❌ ပယ်ဖျက်လိုက်ပါသည်။", reply_markup=MAIN_KEYBOARD)

    elif state == "EDIT_PROD_NAME":
        idx = s_data.get("edit_idx", 0)
        db["products"][idx]["name"] = text
        db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
        save_db(db)
        await bot.send_message(chat_id=chat_id, text="✅ Name ပြင်ဆင်ပြီးပါပြီ။", reply_markup=MAIN_KEYBOARD)

    elif state == "EDIT_PROD_INFO":
        idx = s_data.get("edit_idx", 0)
        db["products"][idx]["info"] = text
        db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
        save_db(db)
        await bot.send_message(chat_id=chat_id, text="✅ Info ပြင်ဆင်ပြီးပါပြီ။", reply_markup=MAIN_KEYBOARD)

    elif state == "SHIP_NAME":
        db["shipping_options"].append({"id": uuid.uuid4().hex[:6], "name": text, "requires_gate": "ဂိတ်" in text})
        db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
        save_db(db)
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Shipping Option **{text}** ကို ထည့်သွင်းပြီးပါပြီ!",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD
        )

    elif state == "PAY_NAME":
        db["bot_sessions"][chat_key] = {"state": "PAY_LOGO", "data": {"new_pay_name": text}}
        save_db(db)
        await bot.send_message(chat_id=chat_id, text="🖼 အဆိုပါ Payment အတွက် **Logo ပုံ** ပို့ပေးပါ -", parse_mode="Markdown")

    elif state == "PAY_LOGO":
        if not update.message.photo:
            await bot.send_message(chat_id=chat_id, text="🖼 ကျေးဇူးပြု၍ Payment Logo ပုံ ပို့ပေးပါ -")
            return
        photo_file = await bot.get_file(update.message.photo[-1].file_id)
        img_bytes = await photo_file.download_as_bytearray()
        logo_b64 = process_logo_image_base64(bytes(img_bytes))
        db["payment_methods"].append({
            "id": uuid.uuid4().hex[:6],
            "name": s_data.get("new_pay_name", "Payment"),
            "logo": logo_b64,
            "qr": "/static/qr.jpg",
            "phone": "09664994195",
            "account_name": "Thura"
        })
        db["bot_sessions"][chat_key] = {"state": "MAIN_MENU", "data": {}}
        save_db(db)
        await bot.send_message(chat_id=chat_id, text="✅ Payment Method ကို ထည့်သွင်းပြီးပါပြီ!", reply_markup=MAIN_KEYBOARD)

# ================= FASTAPI WEB SERVER =================
app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/data")
async def get_shop_data():
    db = load_db()
    return JSONResponse({
        "categories": db.get("categories", []),
        "products": db.get("products", []),
        "shipping_options": db.get("shipping_options", []),
        "payment_methods": db.get("payment_methods", []),
    })

@app.get("/api/set-webhook")
async def setup_telegram_webhook(request: Request):
    base_url = str(request.base_url).rstrip("/")
    if base_url.startswith("http://") and "localhost" not in base_url:
        base_url = "https://" + base_url[len("http://"):]
    webhook_url = f"{base_url}/api/webhook"
    bot = Bot(token=BOT_TOKEN)
    await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    return JSONResponse({"status": "Webhook successfully linked!", "webhook_url": webhook_url})

@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    bot = Bot(token=BOT_TOKEN)
    update = Update.de_json(data, bot)
    await handle_telegram_update(update, bot)
    return JSONResponse({"ok": True})

@app.post("/api/order")
async def submit_order(
    name: str = Form(...), phone1: str = Form(...), phone2: str = Form(""),
    address: str = Form(...), shipping: str = Form(...), bus_line: str = Form(""),
    gate_name: str = Form(""), payment_method: str = Form(...), total: int = Form(...),
    items_json: str = Form(...), slip: UploadFile = File(...)
):
    items = json.loads(items_json)
    order_id = uuid.uuid4().hex[:6].upper()
    date_str = datetime.now().strftime("%d %b %Y")

    slip_bytes = await slip.read()

    order_data = {
        "order_id": order_id, "date": date_str, "name": name, "phone1": phone1, "phone2": phone2,
        "address": address, "shipping": shipping, "bus_line": bus_line, "gate_name": gate_name,
        "payment_method": payment_method, "total": total, "items": items,
    }

    pdf_filename = f"Order_{order_id}.pdf"
    pdf_bytes = generate_order_pdf_bytes(order_data)

    items_text = "\n".join([f"  • {i['name']} (x{i['qty']}) - {int(i['price']) * int(i['qty']):,} Ks" for i in items])
    gate_info = f"\n🚌 *ကားဂိတ်အမည်:* {bus_line}\n📍 *ဂိတ်အမည်:* {gate_name}" if bus_line else ""

    msg_text = (
        f"🔔 *ORDER အသစ်ဝင်လာပါပြီ! (#{order_id})*\n📅 ရက်စွဲ: {date_str}\n\n"
        f"👤 *အမည်:* {name}\n📞 *ဖုန်း (၁):* {phone1}\n📞 *ဖုန်း (၂):* {phone2 or '-'}\n"
        f"🏠 *လိပ်စာ:* {address}\n🚚 *ပို့ဆောင်မှု:* {shipping}{gate_info}\n"
        f"💳 *ငွေပေးချေမှု:* {payment_method}\n\n🛒 *မှာယူထားသော ပစ္စည်းများ:*\n{items_text}\n\n💰 *စုစုပေါင်း:* {total:,} Ks"
    )

    db = load_db()
    bot = Bot(token=BOT_TOKEN)
    for chat_id in db.get("admin_chat_ids", []):
        try:
            photo_stream = io.BytesIO(slip_bytes)
            photo_stream.name = slip.filename or "slip.jpg"
            await bot.send_photo(chat_id=chat_id, photo=photo_stream, caption=msg_text, parse_mode="Markdown")

            pdf_stream = io.BytesIO(pdf_bytes)
            pdf_stream.name = pdf_filename
            await bot.send_document(chat_id=chat_id, document=pdf_stream, filename=pdf_filename)
        except Exception as e:
            print(f"Telegram send error: {e}")

    return JSONResponse({"status": "success", "order": order_data})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)