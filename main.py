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

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ================= DATABASE SETUP =================
def load_db():
    if not os.path.exists(DB_FILE):
        initial_data = {
            "admin_chat_ids": [],
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
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

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
    img.save(save_path, "PNG")
    return f"/static/uploads/{filename}", bg_color, glow_color

# ================= PDF GENERATOR =================
def generate_order_pdf(order_data: dict, pdf_path: str):
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
    pdf.output(pdf_path)

# ================= TELEGRAM BOT HANDLERS =================
(
    ASK_USERNAME,
    ASK_PASSWORD,
    MAIN_MENU,
    PROD_PHOTO,
    PROD_NAME,
    PROD_PRICE,
    SHIP_NAME,
    PAY_NAME,
    PAY_LOGO,
    DELETE_PROD,
) = range(10)

MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["1. Product တင်မည်"],
        ["2. Shipping Option အသစ်တိုးရန်"],
        ["3. Payment Add လုပ်ရန်"],
        ["4. Product ဖျက်မည်"],
    ],
    resize_keyboard=True,
)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔐 Master Admin Panel သို့ ဝင်ရောက်ရန် **Username** ရိုက်ထည့်ပေးပါ -",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
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
        await update.message.reply_text(
            "✅ Login အောင်မြင်ပါပြီ။ လုပ်ဆောင်လိုသည်ကို ရွေးချယ်ပါ -",
            reply_markup=MENU_KEYBOARD,
        )
        return MAIN_MENU
    await update.message.reply_text("❌ Password မှားယွင်းနေပါသည်။ ပြန်လည်ရိုက်ထည့်ပါ -")
    return ASK_PASSWORD

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    if choice.startswith("1"):
        await update.message.reply_text("📸 **Product ပုံ ပို့ပေးပါ** (White background ပုံပို့ပေးပါက အလိုအလျောက် Background ဖျောက်ပေးပါမည်) -", parse_mode="Markdown")
        return PROD_PHOTO
    elif choice.startswith("2"):
        await update.message.reply_text("🚚 အသစ်တိုးမည့် **Shipping Option Name** ကို ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown")
        return SHIP_NAME
    elif choice.startswith("3"):
        await update.message.reply_text("💳 အသစ်ထည့်မည့် **Payment အမည်** ကို ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown")
        return PAY_NAME
    elif choice.startswith("4"):
        db = load_db()
        if not db["products"]:
            await update.message.reply_text("❌ လက်ရှိတွင် ဖျက်စရာ Product မရှိသေးပါ။", reply_markup=MENU_KEYBOARD)
            return MAIN_MENU
        
        msg = "🗑 **ဖျက်လိုသော Product ၏ နံပါတ်ကို ရိုက်ထည့်ပါ**\n\n"
        for i, p in enumerate(db["products"]):
            msg += f"{i+1}. {p['name']} ({p['price']:,} Ks)\n"
        msg += "\n(မဖျက်လိုပါက `cancel` ဟု ရိုက်ထည့်နိုင်ပါသည်)"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        return DELETE_PROD
    else:
        await update.message.reply_text("မီနူးမှ နံပါတ်တစ်ခုကို ရွေးချယ်ပေးပါ -", reply_markup=MENU_KEYBOARD)
        return MAIN_MENU

# --- Flow 1: Add Product ---
async def receive_prod_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("ကျေးဇူးပြု၍ Product ပုံ (Photo) ပို့ပေးပါ -")
        return PROD_PHOTO

    photo_file = await update.message.photo[-1].get_file()
    img_bytes = await photo_file.download_as_bytearray()
    filename = f"prod_{uuid.uuid4().hex[:8]}.png"

    await update.message.reply_text("⏳ ပုံကို Auto Background ဖျောက်ပြီး အရောင်ချိန်ညှိနေပါသည်...")
    img_url, bg_color, glow_color = process_product_image(bytes(img_bytes), filename)

    context.user_data["new_prod"] = {
        "id": uuid.uuid4().hex[:8],
        "image": img_url,
        "bg_color": bg_color,
        "glow_color": glow_color,
    }
    await update.message.reply_text("🏷 **Product Name** ပေးပါ -", parse_mode="Markdown")
    return PROD_NAME

async def receive_prod_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_prod"]["name"] = update.message.text.strip()
    await update.message.reply_text("💰 **Price** (စျေးနှုန်း - ဂဏန်းသီးသန့်) ရိုက်ထည့်ပေးပါ -", parse_mode="Markdown")
    return PROD_PRICE

async def receive_prod_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price_str = update.message.text.strip().replace(",", "").replace("Ks", "").strip()
    if not price_str.isdigit():
        await update.message.reply_text("ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပေးပါ (ဥပမာ - 15000) -")
        return PROD_PRICE

    prod = context.user_data["new_prod"]
    prod["price"] = int(price_str)

    db = load_db()
    db["products"].append(prod)
    save_db(db)

    await update.message.reply_text(
        f"✅ **{prod['name']}** ({prod['price']:,} Ks) ကို Web ပေါ်သို့ အောင်မြင်စွာ တင်ပြီးပါပြီ!",
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD,
    )
    return MAIN_MENU

# --- Flow 4: Delete Product ---
async def receive_delete_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text.lower() == 'cancel':
        await update.message.reply_text("❌ လုပ်ဆောင်ချက်ကို ပယ်ဖျက်လိုက်ပါသည်။", reply_markup=MENU_KEYBOARD)
        return MAIN_MENU
        
    if not text.isdigit():
        await update.message.reply_text("ဂဏန်းသီးသန့်သာ ရိုက်ထည့်ပါ -")
        return DELETE_PROD
        
    idx = int(text) - 1
    db = load_db()
    
    if 0 <= idx < len(db["products"]):
        deleted_prod = db["products"].pop(idx)
        save_db(db)
        
        try:
            img_path = deleted_prod.get("image", "").lstrip("/")
            full_path = os.path.join(BASE_DIR, img_path)
            if os.path.exists(full_path):
                os.remove(full_path)
        except Exception:
            pass
            
        await update.message.reply_text(
            f"✅ **{deleted_prod['name']}** ကို အောင်မြင်စွာ ဖျက်လိုက်ပါပြီ။ Web တွင် ချက်ချင်းပျောက်သွားပါမည်။", 
            parse_mode="Markdown",
            reply_markup=MENU_KEYBOARD
        )
        return MAIN_MENU
    else:
        await update.message.reply_text("❌ နံပါတ် မှားယွင်းနေပါသည်။ စာရင်းတွင်ပါသော နံပါတ်ကို ပြန်လည်ရွေးချယ်ပါ -")
        return DELETE_PROD

# --- Flow 2: Add Shipping ---
async def receive_ship_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ship_name = update.message.text.strip()
    db = load_db()
    requires_gate = "ဂိတ်" in ship_name or "gate" in ship_name.lower()
    db["shipping_options"].append({
        "id": uuid.uuid4().hex[:6],
        "name": ship_name,
        "requires_gate": requires_gate
    })
    save_db(db)
    await update.message.reply_text(
        f"✅ Shipping Option **{ship_name}** ကို Web ထဲသို့ ချက်ချင်း ထည့်သွင်းပြီးပါပြီ!",
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD,
    )
    return MAIN_MENU

# --- Flow 3: Add Payment ---
async def receive_pay_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_pay_name"] = update.message.text.strip()
    await update.message.reply_text("🖼 အဆိုပါ Payment အတွက် **Logo ပုံ** ပို့ပေးပါ -", parse_mode="Markdown")
    return PAY_LOGO

async def receive_pay_logo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("ကျေးဇူးပြု၍ Payment Logo ပုံ ပို့ပေးပါ -")
        return PAY_LOGO

    photo_file = await update.message.photo[-1].get_file()
    filename = f"pay_{uuid.uuid4().hex[:6]}.jpg"
    save_path = os.path.join(UPLOAD_DIR, filename)
    await photo_file.download_to_drive(save_path)

    pay_name = context.user_data["new_pay_name"]
    db = load_db()
    db["payment_methods"].append({
        "id": uuid.uuid4().hex[:6],
        "name": pay_name,
        "logo": f"/static/uploads/{filename}",
        "qr": "/static/qr.jpg",
        "phone": "09664994195",
        "account_name": "Thura"
    })
    save_db(db)

    await update.message.reply_text(
        f"✅ Payment Method **{pay_name}** ကို Web ထဲသို့ ချက်ချင်း ထည့်သွင်းပြီးပါပြီ!",
        parse_mode="Markdown",
        reply_markup=MENU_KEYBOARD,
    )
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
        telegram_app = Application.builder().token(BOT_TOKEN).build()
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start_cmd)],
            states={
                ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_username)],
                ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_password)],
                MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu)],
                PROD_PHOTO: [MessageHandler(filters.PHOTO, receive_prod_photo)],
                PROD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prod_name)],
                PROD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prod_price)],
                DELETE_PROD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_delete_prod)],
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

@app.on_event("shutdown")
async def shutdown_event():
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/data")
async def get_shop_data():
    return JSONResponse(load_db())

@app.post("/api/order")
async def submit_order(
    name: str = Form(...),
    phone1: str = Form(...),
    phone2: str = Form(""),
    address: str = Form(...),
    shipping: str = Form(...),
    bus_line: str = Form(""),
    gate_name: str = Form(""),
    payment_method: str = Form(...),
    total: int = Form(...),
    items_json: str = Form(...),
    slip: UploadFile = File(...),
):
    items = json.loads(items_json)
    order_id = uuid.uuid4().hex[:6].upper()
    date_str = datetime.now().strftime("%d %b %Y")

    slip_ext = os.path.splitext(slip.filename)[1] or ".jpg"
    slip_filename = f"slip_{order_id}{slip_ext}"
    slip_path = os.path.join(UPLOAD_DIR, slip_filename)
    with open(slip_path, "wb") as f:
        f.write(await slip.read())

    order_data = {
        "order_id": order_id,
        "date": date_str,
        "name": name,
        "phone1": phone1,
        "phone2": phone2,
        "address": address,
        "shipping": shipping,
        "bus_line": bus_line,
        "gate_name": gate_name,
        "payment_method": payment_method,
        "total": total,
        "items": items,
    }

    pdf_filename = f"Order_{order_id}.pdf"
    pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
    generate_order_pdf(order_data, pdf_path)

    items_text = "\n".join(
        [f"  • {i['name']} (x{i['qty']}) - {int(i['price']) * int(i['qty']):,} Ks" for i in items]
    )
    gate_info = ""
    if bus_line or gate_name:
        gate_info = f"\n🚌 **ကားဂိတ်အမည်:** {bus_line}\n📍 **ဂိတ်အမည်:** {gate_name}"

    msg_text = (
        f"🔔 **ORDER အသစ်ဝင်လာပါပြီ! (#{order_id})**\n"
        f"📅 ရက်စွဲ: {date_str}\n\n"
        f"👤 **မှာယူသူအမည်:** {name}\n"
        f"📞 **ဖုန်းနံပါတ် (၁):** {phone1}\n"
        f"📞 **ဖုန်းနံပါတ် (၂):** {phone2 or '-'}\n"
        f"🏠 **လိပ်စာ:** {address}\n"
        f"🚚 **ပို့ဆောင်မှုစနစ်:** {shipping}{gate_info}\n"
        f"💳 **ငွေပေးချေမှု:** {payment_method}\n\n"
        f"🛒 **မှာယူထားသော ပစ္စည်းများ:**\n{items_text}\n\n"
        f"💰 **စုစုပေါင်းကျသင့်ငွေ:** {total:,} Ks"
    )

    db = load_db()
    if telegram_app and db["admin_chat_ids"]:
        for chat_id in db["admin_chat_ids"]:
            try:
                with open(slip_path, "rb") as photo_f:
                    await telegram_app.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_f,
                        caption=msg_text,
                        parse_mode="Markdown",
                    )
                with open(pdf_path, "rb") as pdf_f:
                    await telegram_app.bot.send_document(
                        chat_id=chat_id,
                        document=pdf_f,
                        filename=pdf_filename,
                        caption=f"📄 Order #{order_id} အတွက် PDF ပြေစာ",
                    )
            except Exception as e:
                print(f"Telegram send error: {e}")

    return JSONResponse({"status": "success", "order": order_data})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)