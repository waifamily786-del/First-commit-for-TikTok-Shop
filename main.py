import os
import io
import json
import uuid
import asyncio
from datetime import datetime
from typing import Optional
from PIL import Image
from fpdf import FPDF

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from telegram import Bot, Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================= CONFIGURATION =================
BOT_TOKEN = "9053956555:AAF2pUW5OkLbsMSVJLnMOJuxIEg4NWYE_TE"
ADMIN_USER = "Junide"
ADMIN_PASS = "forevertogether2006"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
DB_FILE = os.path.join(BASE_DIR, "database.json")

# Vercel ကဲ့သို့ Read-only environment များတွင် Error မတက်စေရန်
try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
except OSError:
    pass

# ================= DATABASE SETUP =================
def load_db():
    if not os.path.exists(DB_FILE):
        initial_data = {
            "admin_chat_ids": [],
            "categories": [{"id": "all", "name": "General"}],
            "products": [],
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
            ]
        }
        save_db(initial_data)
        return initial_data
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        if "categories" not in data:
            data["categories"] = [{"id": "all", "name": "General"}]
        return data

def save_db(data):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Database save error (Read-only storage): {e}")

# ================= IMAGE PROCESSING (FAST WHITE-BG REMOVER) =================
def process_product_image(image_bytes: bytes, filename: str):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
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

    save_path = os.path.join(UPLOAD_DIR, filename)
    try:
        img.save(save_path, "PNG")
    except Exception:
        pass
    return f"/static/uploads/{filename}", bg_color, glow_color

# ================= PDF GENERATOR (BYTES MEMORY) =================
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

# ================= TELEGRAM BOT HANDLERS =================
(
    ASK_USERNAME, ASK_PASSWORD, MAIN_MENU,
    PROD_PHOTO, PROD_NAME, PROD_INFO, PROD_CAT, PROD_PRICE,
    ADD_CAT, MANAGE_PROD_SELECT, MANAGE_PROD_ACTION, 
    EDIT_PROD_NAME, EDIT_PROD_INFO,
    SHIP_NAME, PAY_NAME, PAY_LOGO,
) = range(16)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["1. Product အသစ်တင်ရန်", "2. Category အသစ်တိုးရန်"],
        ["3. Product စီမံရန် (ပြင်/ဖျက်/Stock)"],
        ["4. Shipping Option တိုးရန်", "5. Payment Add လုပ်ရန်"],
    ],
    resize_keyboard=True,
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔐 Master Admin Panel သို့ ဝင်ရောက်ရန် **Username** ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return ASK_USERNAME

async def check_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == ADMIN_USER:
        await update.message.reply_text("🔑 **Password** ဆက်လက်ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown")
        return ASK_PASSWORD
    await update.message.reply_text("❌ Username မှားယွင်းနေပါသည်။ ပြန်လည်ရိုက်ထည့်ပါ -")
    return ASK_USERNAME

async def check_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == ADMIN_PASS:
        db = load_db()
        chat_id = update.effective_chat.id
        if chat_id not in db["admin_chat_ids"]:
            db["admin_chat_ids"].append(chat_id)
            save_db(db)
        await update.message.reply_text("✅ Login အောင်မြင်ပါပြီ။ လုပ်ဆောင်လိုသည်ကို ရွေးချယ်ပါ -", reply_markup=MAIN_KEYBOARD)
        return MAIN_MENU
    await update.message.reply_text("❌ Password မှားယွင်းနေပါသည်။ ပြန်လည်ရိုက်ထည့်ပါ -")
    return ASK_PASSWORD

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    if choice.startswith("1"):
        await update.message.reply_text("📸 **Product ပုံ ပို့ပေးပါ** (White background) -", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return PROD_PHOTO
    elif choice.startswith("2"):
        await update.message.reply_text("📂 အသစ်တိုးမည့် **Category Name** ကို ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return ADD_CAT
    elif choice.startswith("3"):
        db = load_db()
        if not db["products"]:
            await update.message.reply_text("❌ စီမံရန် Product မရှိသေးပါ။", reply_markup=MAIN_KEYBOARD)
            return MAIN_MENU
        msg = "⚙️ **စီမံလိုသော Product ၏ နံပါတ်ကို ရိုက်ထည့်ပါ**\n\n"
        for i, p in enumerate(db["products"]):
            status = "[🔴 Out of Stock]" if p.get("out_of_stock") else "[🟢 In Stock]"
            msg += f"{i+1}. {p['name']} {status}\n"
        msg += "\n(မလုပ်တော့ပါက `cancel` ဟု ရိုက်ထည့်ပါ)"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return MANAGE_PROD_SELECT
    elif choice.startswith("4"):
        await update.message.reply_text("🚚 အသစ်တိုးမည့် **Shipping Option Name** ကို ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return SHIP_NAME
    elif choice.startswith("5"):
        await update.message.reply_text("💳 အသစ်ထည့်မည့် **Payment အမည်** ကို ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return PAY_NAME
    else:
        await update.message.reply_text("မီနူးမှ ရွေးချယ်ပေးပါ -", reply_markup=MAIN_KEYBOARD)
        return MAIN_MENU

# --- Flow 2: Add Category ---
async def receive_add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat_name = update.message.text.strip()
    db = load_db()
    db["categories"].append({"id": uuid.uuid4().hex[:6], "name": cat_name})
    save_db(db)
    await update.message.reply_text(f"✅ Category **{cat_name}** ကို ထည့်သွင်းပြီးပါပြီ!", parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU

# --- Flow 1: Add Product ---
async def receive_prod_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        return PROD_PHOTO
    photo_file = await update.message.photo[-1].get_file()
    img_bytes = await photo_file.download_as_bytearray()
    filename = f"prod_{uuid.uuid4().hex[:8]}.png"
    await update.message.reply_text("⏳ ပုံကို Auto Background ဖျောက်နေပါသည်...")
    img_url, bg_color, glow_color = process_product_image(bytes(img_bytes), filename)
    context.user_data["new_prod"] = {
        "id": uuid.uuid4().hex[:8],
        "image": img_url,
        "bg_color": bg_color,
        "glow_color": glow_color,
        "out_of_stock": False
    }
    await update.message.reply_text("🏷 **Product Name** ပေးပါ -", parse_mode="Markdown")
    return PROD_NAME

async def receive_prod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_prod"]["name"] = update.message.text.strip()
    await update.message.reply_text("📝 **Product Info** (Product Name အောက်တွင်ပေါ်မည့် အချက်အလက်အရှည်) ကို ရိုက်ထည့်ပါ -", parse_mode="Markdown")
    return PROD_INFO

async def receive_prod_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_prod"]["info"] = update.message.text.strip()
    db = load_db()
    cat_buttons = [[c["name"]] for c in db["categories"]]
    await update.message.reply_text("📂 **မည်သည့် Category အောက်တွင် ထားမည်နည်း?**", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(cat_buttons, resize_keyboard=True))
    return PROD_CAT

async def receive_prod_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_prod"]["category"] = update.message.text.strip()
    await update.message.reply_text("💰 **Price** (စျေးနှုန်း - ဂဏန်းသီးသန့်) ရိုက်ထည့်ပါ -", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return PROD_PRICE

async def receive_prod_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price_str = update.message.text.strip().replace(",", "").replace("Ks", "").strip()
    if not price_str.isdigit():
        await update.message.reply_text("ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ -")
        return PROD_PRICE
    prod = context.user_data["new_prod"]
    prod["price"] = int(price_str)
    db = load_db()
    db["products"].append(prod)
    save_db(db)
    await update.message.reply_text(f"✅ **{prod['name']}** ကို Web သို့ တင်ပြီးပါပြီ!", parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU

# --- Flow 3: Manage Product ---
async def receive_manage_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == 'cancel':
        await update.message.reply_text("❌ ပယ်ဖျက်လိုက်ပါသည်။", reply_markup=MAIN_KEYBOARD)
        return MAIN_MENU
    if not text.isdigit():
        return MANAGE_PROD_SELECT
    idx = int(text) - 1
    db = load_db()
    if 0 <= idx < len(db["products"]):
        context.user_data["edit_idx"] = idx
        p = db["products"][idx]
        status = "🔴 Out of Stock" if p.get("out_of_stock") else "🟢 In Stock"
        msg = f"📦 **{p['name']}**\nInfo: {p.get('info', '-')}\nStatus: {status}\n\nမည်သို့ လုပ်ဆောင်လိုသနည်း?"
        kb = [["1. Name ပြင်မည်", "2. Info ပြင်မည်"], ["3. Stock အခြေအနေပြောင်းမည်"], ["4. အပြီးဖျက်မည်", "Cancel"]]
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return MANAGE_PROD_ACTION
    return MANAGE_PROD_SELECT

async def receive_manage_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    idx = context.user_data.get("edit_idx")
    db = load_db()
    if choice.startswith("1"):
        await update.message.reply_text("🏷 Product Name အသစ် ရိုက်ထည့်ပါ -", reply_markup=ReplyKeyboardRemove())
        return EDIT_PROD_NAME
    elif choice.startswith("2"):
        await update.message.reply_text("📝 Product Info အသစ် ရိုက်ထည့်ပါ -", reply_markup=ReplyKeyboardRemove())
        return EDIT_PROD_INFO
    elif choice.startswith("3"):
        db["products"][idx]["out_of_stock"] = not db["products"][idx].get("out_of_stock", False)
        save_db(db)
        await update.message.reply_text("✅ Stock အခြေအနေ ပြောင်းလဲပြီးပါပြီ။", reply_markup=MAIN_KEYBOARD)
        return MAIN_MENU
    elif choice.startswith("4"):
        deleted = db["products"].pop(idx)
        save_db(db)
        await update.message.reply_text(f"✅ {deleted['name']} ကို အပြီးဖျက်လိုက်ပါပြီ။", reply_markup=MAIN_KEYBOARD)
        return MAIN_MENU
    else:
        await update.message.reply_text("❌ ပယ်ဖျက်လိုက်ပါသည်။", reply_markup=MAIN_KEYBOARD)
        return MAIN_MENU

async def receive_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    idx = context.user_data.get("edit_idx")
    db["products"][idx]["name"] = update.message.text.strip()
    save_db(db)
    await update.message.reply_text("✅ Name ပြင်ဆင်ပြီးပါပြီ။", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU

async def receive_edit_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    idx = context.user_data.get("edit_idx")
    db["products"][idx]["info"] = update.message.text.strip()
    save_db(db)
    await update.message.reply_text("✅ Info ပြင်ဆင်ပြီးပါပြီ။", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU

# --- Flow 4 & 5: Shipping and Payment ---
async def receive_ship_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ship_name = update.message.text.strip()
    db = load_db()
    db["shipping_options"].append({"id": uuid.uuid4().hex[:6], "name": ship_name, "requires_gate": "ဂိတ်" in ship_name})
    save_db(db)
    await update.message.reply_text(f"✅ Shipping Option **{ship_name}** ကို ထည့်သွင်းပြီးပါပြီ!", parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU

async def receive_pay_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_pay_name"] = update.message.text.strip()
    await update.message.reply_text("🖼 အဆိုပါ Payment အတွက် **Logo ပုံ** ပို့ပေးပါ -", parse_mode="Markdown")
    return PAY_LOGO

async def receive_pay_logo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return PAY_LOGO
    photo_file = await update.message.photo[-1].get_file()
    filename = f"pay_{uuid.uuid4().hex[:6]}.jpg"
    try:
        await photo_file.download_to_drive(os.path.join(UPLOAD_DIR, filename))
    except Exception:
        pass
    db = load_db()
    db["payment_methods"].append({
        "id": uuid.uuid4().hex[:6], "name": context.user_data["new_pay_name"],
        "logo": f"/static/uploads/{filename}", "qr": "/static/qr.jpg",
        "phone": "09664994195", "account_name": "Thura"
    })
    save_db(db)
    await update.message.reply_text("✅ Payment Method ကို ထည့်သွင်းပြီးပါပြီ!", reply_markup=MAIN_KEYBOARD)
    return MAIN_MENU

# ================= FASTAPI WEB SERVER =================
app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

telegram_app: Optional[Application] = None

@app.on_event("startup")
async def startup_event():
    global telegram_app
    load_db()
    if BOT_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        try:
            telegram_app = Application.builder().token(BOT_TOKEN).build()
            conv_handler = ConversationHandler(
                entry_points=[CommandHandler("start", start_cmd)],
                states={
                    ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_username)],
                    ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_password)],
                    MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu)],
                    PROD_PHOTO: [MessageHandler(filters.PHOTO, receive_prod_photo)],
                    PROD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prod_name)],
                    PROD_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prod_info)],
                    PROD_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prod_cat)],
                    PROD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prod_price)],
                    ADD_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_add_cat)],
                    MANAGE_PROD_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manage_select)],
                    MANAGE_PROD_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manage_action)],
                    EDIT_PROD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_name)],
                    EDIT_PROD_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_info)],
                    SHIP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ship_name)],
                    PAY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_pay_name)],
                    PAY_LOGO: [MessageHandler(filters.PHOTO, receive_pay_logo)],
                },
                fallbacks=[CommandHandler("start", start_cmd)],
            )
            telegram_app.add_handler(conv_handler)
            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.updater.start_polling()
        except Exception as e:
            print(f"Telegram polling startup note: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    if telegram_app and telegram_app.updater:
        try:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception:
            pass

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/data")
async def get_shop_data():
    return JSONResponse(load_db())

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

    # ငွေလွှဲပြေစာပုံကို Server ထဲသို့ ဖိုင်အဖြစ်မသိမ်းဘဲ RAM (Bytes) ထဲသို့ ဖတ်ယူခြင်း
    slip_bytes = await slip.read()

    order_data = {
        "order_id": order_id, "date": date_str, "name": name, "phone1": phone1, "phone2": phone2,
        "address": address, "shipping": shipping, "bus_line": bus_line, "gate_name": gate_name,
        "payment_method": payment_method, "total": total, "items": items,
    }

    # PDF Invoice ကို Memory (Bytes) ထဲတွင်သာ တိုက်ရိုက်ထုတ်ခြင်း
    pdf_filename = f"Order_{order_id}.pdf"
    pdf_bytes = generate_order_pdf_bytes(order_data)

    items_text = "\n".join([f"  • {i['name']} (x{i['qty']}) - {int(i['price']) * int(i['qty']):,} Ks" for i in items])
    gate_info = f"\n🚌 **ကားဂိတ်အမည်:** {bus_line}\n📍 **ဂိတ်အမည်:** {gate_name}" if bus_line else ""

    msg_text = (
        f"🔔 **ORDER အသစ်ဝင်လာပါပြီ! (#{order_id})**\n📅 ရက်စွဲ: {date_str}\n\n"
        f"👤 **အမည်:** {name}\n📞 **ဖုန်း (၁):** {phone1}\n📞 **ဖုန်း (၂):** {phone2 or '-'}\n"
        f"🏠 **လိပ်စာ:** {address}\n🚚 **ပို့ဆောင်မှု:** {shipping}{gate_info}\n"
        f"💳 **ငွေပေးချေမှု:** {payment_method}\n\n🛒 **မှာယူထားသော ပစ္စည်းများ:**\n{items_text}\n\n💰 **စုစုပေါင်း:** {total:,} Ks"
    )

    # Telegram Bot မှတစ်ဆင့် Admin ဆီသို့ ပုံနှင့် PDF ကို Memory ကနေ တိုက်ရိုက်ပို့ခြင်း
    db = load_db()
    try:
        bot = Bot(token=BOT_TOKEN)
        for chat_id in db.get("admin_chat_ids", []):
            try:
                photo_stream = io.BytesIO(slip_bytes)
                photo_stream.name = slip.filename or "slip.jpg"
                await bot.send_photo(chat_id=chat_id, photo=photo_stream, caption=msg_text, parse_mode="Markdown")

                pdf_stream = io.BytesIO(pdf_bytes)
                pdf_stream.name = pdf_filename
                await bot.send_document(chat_id=chat_id, document=pdf_stream, filename=pdf_filename)
            except Exception as ex:
                print(f"Telegram send inner error: {ex}")
    except Exception as e:
        print(f"Telegram bot init error: {e}")

    return JSONResponse({"status": "success", "order": order_data})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)