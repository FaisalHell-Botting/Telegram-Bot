import sqlite3
import logging
import asyncio
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes

# --- الإعدادات ---
TOKEN = '8705243157:AAEvgDT3PecE8fmwc962NnToHnJl2xpFhAQ'
CASHIER_ID = 5312266808
DB_NAME = 'orders_v2.db'

# --- إعدادات السيرفر والـ Webhook ---
# تم وضع الدومين الخاص بك هان
WEBHOOK_DOMAIN = "https://lego-food-bot.onrender.com"
PORT = int(os.environ.get('PORT', 8443)) 

PRICES = {
    'شاي': 1, 'قهوة مزاج وسط': 2, 'قهوة مزاج كبير': 3, 'نسكافيه مكس': 2, 'كفي مكس': 2, 'كابتشينو جوداي': 3,
    'كوكاكولا': 4, 'بلو أزرق': 4, 'مراعي حليب شوكلاتة': 2, 'عصير كوكتيل فواكه': 2, 'لتر عصير برتقال': 7, 'لتر عصير مانجا': 7,
    'سندويش فينو فيتا': 3, 'سندويش فينو مرتديلا': 3,
    'سنيكرز': 3, 'تويكس': 3, 'مارس': 3, 'مستر بايت': 4, 'قسماط حجم وسط': 4, 'بسكويت مالح': 2, 
    'بسكويت ديمة فانيلا': 2, 'مولتو ميني': 2, 'شكلاتة تجارية ب 2شيكل': 2, 'شكلاتة تجارية ب 1 شيكل': 1, 'حلو نعنع سكوتش': 1,
    'برنجلز أحمر صغير': 6, 'برنجلز أحمر كبير': 11, 'برنجلز أحمر كبير شطة': 11, 'كيك فراولة': 7
}

CHOOSING_CATEGORY, CHOOSING_SERVICE, CONFIRMING_CART, LOCATION_TYPE, OFFICE_NUMBER = range(5)
SETTLING_DEBT = 20

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- قاعدة البيانات ---
def init_db():
    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                 details TEXT, total_price INTEGER, location TEXT, timestamp TEXT, 
                 status TEXT, is_paid INTEGER DEFAULT 0)''')
    conn.commit(); conn.close()

# --- واجهة السلة والطلب ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cart'] = []
    return await show_categories(update, context)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("☕ مشروبات ساخنة", callback_data='cat_hot')],
                [InlineKeyboardButton("🥤 مشروبات باردة", callback_data='cat_cold')],
                [InlineKeyboardButton("🥪 سندويشات (بالمكان فقط)", callback_data='cat_sandwiches')],
                [InlineKeyboardButton("🍫 شوكلاتة", callback_data='cat_choc')],
                [InlineKeyboardButton("🍟 شبسي", callback_data='cat_chips')]]
    text = 'يسعد أوقاتك! تفضل اختار القسم:'
    if hasattr(update, 'callback_query') and update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_CATEGORY

async def category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    menu_map = {
        'cat_hot': ['شاي', 'قهوة مزاج وسط', 'قهوة مزاج كبير', 'نسكافيه مكس', 'كفي مكس', 'كابتشينو جوداي'],
        'cat_cold': ['كوكاكولا', 'بلو أزرق', 'مراعي حليب شوكلاتة', 'عصير كوكتيل فواكه', 'لتر عصير برتقال', 'لتر عصير مانجا'],
        'cat_sandwiches': ['سندويش فينو فيتا', 'سندويش فينو مرتديلا'],
        'cat_choc': ['سنيكرز', 'تويكس', 'مارس', 'مستر بايت', 'قسماط حجم وسط', 'بسكويت مالح', 'بسكويت ديمة فانيلا', 'مولتو ميني', 'شكلاتة تجارية ب 2شيكل', 'شكلاتة تجارية ب 1 شيكل', 'حلو نعنع سكوتش'],
        'cat_chips': ['برنجلز أحمر صغير', 'برنجلز أحمر كبير', 'برنجلز أحمر كبير شطة', 'كيك فراولة']
    }
    items = menu_map.get(query.data, [])
    keyboard = [[InlineKeyboardButton(f"{item} ({PRICES[item]} ش)", callback_data=item)] for item in items]
    keyboard.append([InlineKeyboardButton("⬅️ رجوع", callback_data='back_to_main')])
    await query.edit_message_text(text="اختار الصنف:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_SERVICE

async def service_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == 'back_to_main': return await show_categories(update, context)
    context.user_data.setdefault('cart', []).append(query.data)
    total = sum(PRICES[item] for item in context.user_data['cart'])
    cart_list = "\n".join([f"• {item}" for item in context.user_data['cart']])
    keyboard = [[InlineKeyboardButton(f"❌ حذف {item}", callback_data=f'remove_{i}')] for i, item in enumerate(context.user_data['cart'])]
    keyboard.append([InlineKeyboardButton("➕ إضافة أصناف", callback_data='add_more')])
    keyboard.append([InlineKeyboardButton("✅ تأكيد الطلب", callback_data='confirm_order')])
    await query.edit_message_text(text=f"🛒 سلتك:\n{cart_list}\n\n💰 المجموع: {total} شيكل", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRMING_CART

async def confirm_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == 'add_more': return await show_categories(update, context)
    elif query.data.startswith('remove_'):
        idx = int(query.data.split('_')[1]); context.user_data['cart'].pop(idx)
        return await service_choice(update, context)
    
    has_sandwich = any("سندويش" in item for item in context.user_data.get('cart', []))
    keyboard = [[InlineKeyboardButton("ع المكتب 🖥️", callback_data='مكتب'), InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]]
    if has_sandwich: keyboard = [[InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]]
    await query.edit_message_text(text="وين حابب تستلم الطلب؟", reply_markup=InlineKeyboardMarkup(keyboard))
    return LOCATION_TYPE

async def location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    context.user_data['location_type'] = query.data
    if query.data == 'مكتب':
        await query.edit_message_text(text="اكتبلي رقم مكتبك:"); return OFFICE_NUMBER
    else:
        context.user_data['office'] = "حجز مكان"; return await send_order(update, context)

async def get_office(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['office'] = f"مكتب {update.message.text}"; return await send_order(update, context)

async def send_order(update, context):
    user = (update.callback_query.from_user if update.callback_query else update.message.from_user)
    cart = context.user_data['cart']; details = ", ".join(cart); total = sum(PRICES[item] for item in cart); office = context.user_data['office']
    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (user.id, details, total, office, datetime.now().strftime("%Y-%m-%d %H:%M"), "انتظار", 0))
    order_id = c.lastrowid; conn.commit(); conn.close()
    
    keyboard = [[InlineKeyboardButton("✅ تأكيد وإضافة للدين", callback_data=f"conf_{user.id}_{order_id}")],
                [InlineKeyboardButton("⚠️ صنف ناقص", callback_data=f"out_{user.id}_{order_id}")]]
    await context.bot.send_message(chat_id=CASHIER_ID, text=f"🚨 **طلب #{order_id}**\n👤 {user.first_name}\n📦 {details}\n📍 {office}\n💰 {total} ش", reply_markup=InlineKeyboardMarkup(keyboard))
    msg = "تم الإرسال للكاشير. حنبلغك أول ما يضاف للدين. ⏳"
    if update.callback_query: await update.callback_query.edit_message_text(msg)
    else: await update.message.reply_text(msg)
    return ConversationHandler.END

# --- وظيفة سجل ديون المستخدم ---
async def user_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute("SELECT id, timestamp, total_price, details FROM orders WHERE user_id=? AND is_paid=0 ORDER BY id DESC", (user_id,))
    rows = c.fetchall(); conn.close()
    if not rows: return await update.message.reply_text("سجلك نظيف! ما عليك أي ديون حالياً. ✨")
    
    report = "📋 **سجل ديونك غير المدفوعة:**\n" + "➖"*10 + "\n"
    grand_total = 0
    for r in rows:
        report += f"🆔 #{r[0]} | 📅 {r[1]}\n📦 {r[3]}\n💰 القيمة: {r[2]} شيكل\n" + "➖"*10 + "\n"
        grand_total += r[2]
    report += f"\n🔴 **إجمالي الدين المطلوب: {grand_total} شيكل**"
    await update.message.reply_text(report)

# --- وظائف الإدارة ---
async def admin_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID: return
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE timestamp >= ?", (week_ago,))
    stats = c.fetchone()
    c.execute("SELECT SUM(total_price) FROM orders WHERE timestamp >= ? AND is_paid=0", (week_ago,))
    debt_total = c.fetchone()[0] or 0
    c.execute("SELECT user_id, location, SUM(total_price) FROM orders WHERE is_paid=0 GROUP BY user_id")
    debtors = c.fetchall(); conn.close()
    
    text = f"📔 **دفتر الدين (آخر 7 أيام):**\n📦 إجمالي الطلبات للكل: {stats[0]}\n💰 المبيعات الكلية: {stats[1] or 0} ش\n🔴 الدين الإجمالي: {debt_total} ش\n\n**قائمة المكاتب المديونة:**"
    keyboard = [[InlineKeyboardButton(f"🔔 تذكير {d[1]} ({d[2]} ش)", callback_data=f"remind_{d[0]}_{d[2]}")] for d in debtors]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def history_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID: return
    keyboard = [[InlineKeyboardButton(f"📅 طلبات يوم {(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')}", callback_data=f"hdate_{(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')}")] for i in range(7)]
    await update.message.reply_text("اختار اليوم اللي بدك تشوف سجل طلباته:", reply_markup=InlineKeyboardMarkup(keyboard))

async def history_day_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    target_date = query.data.split("_")[1]
    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute("SELECT id, details, total_price, location, timestamp FROM orders WHERE timestamp LIKE ? ORDER BY id ASC", (f"{target_date}%",))
    rows = c.fetchall(); conn.close()
    if not rows: return await query.edit_message_text(f"ما في طلبات مسجلة ليوم {target_date}.")
    report = f"📊 **تقرير يوم: {target_date}**\n📦 الطلبات: {len(rows)}\n💰 الدخل: {sum(r[2] for r in rows)} شيكل\n" + "═"*10 + "\n"
    for r in rows: report += f"#{r[0]} | 🕓 {r[4].split()[1]} | 📍 {r[3]}\n📑 {r[1]}\n💰 {r[2]} شيكل\n" + "➖"*10 + "\n"
    await query.edit_message_text(report)

async def send_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, user_id, amount = query.data.split("_")
    text = f"🔔 **تذكير بالدفع**\nيسعد أوقاتك! عليك مستحقات متراكمة بقيمة **{amount} شيكل**.\nيرجى تسديد المبلغ لتصفير السجل."
    keyboard = [[InlineKeyboardButton("💳 تسديد الآن", callback_data=f"settle_{amount}")]]
    try:
        await context.bot.send_message(chat_id=user_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
        await query.edit_message_text(query.message.text + f"\n\n✅ تم إرسال تذكير للمكتب.")
    except: await query.answer("تعذر الإرسال")

# --- دورة تسديد الدين ---
async def settle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    amount = query.data.split("_")[1]
    context.user_data['settle_amount'] = amount
    await query.edit_message_text(f"تمام! المبلغ المطلوب: **{amount} شيكل**.\nحوّل للمحفظة:\n`0597489605` (كمال عبيد)\n\nوارفع صورة الإيصال هان 👇")
    return SETTLING_DEBT

async def receive_debt_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    amount = context.user_data.get('settle_amount', '?')
    await update.message.reply_text("شكراً لك على تسديد المستحقات! ❤️\nوصل الإشعار للكاشير للتأكيد.")
    keyboard = [[InlineKeyboardButton("✅ تأكيد الاستلام وتصفير الحساب", callback_data=f"clear_{user.id}")]]
    caption = f"💰 **إشعار تسديد دين!**\n👤 {user.first_name}\n💰 المبلغ: {amount} ش"
    if update.message.photo: await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id, caption=caption, reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.message.document: await context.bot.send_document(chat_id=CASHIER_ID, document=update.message.document.file_id, caption=caption, reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def clear_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    user_id = query.data.split("_")[1]
    conn = sqlite3.connect(DB_NAME); c = conn.cursor()
    c.execute("UPDATE orders SET is_paid=1 WHERE user_id=? AND is_paid=0", (user_id,))
    conn.commit(); conn.close()
    try:
        await query.edit_message_caption(caption="✅ تم تصفير حساب المكتب بنجاح في قاعدة البيانات.")
        await context.bot.send_message(chat_id=user_id, text="✅ تم تأكيد استلام المبلغ وتصفير حسابك. شكراً لك! 🌸")
        await context.bot.send_message(chat_id=CASHIER_ID, text="تم إرسال رسالة التصفير للزبون بنجاح. ✉️")
    except Exception as e:
        await context.bot.send_message(chat_id=CASHIER_ID, text="⚠️ تم تصفير الحساب، لكن حدث خطأ في رسالة الشكر.")

async def cashier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    if data[0] == "conf":
        conn = sqlite3.connect(DB_NAME); c = conn.cursor()
        c.execute("UPDATE orders SET status='مقبول' WHERE id=?", (data[2],))
        conn.commit(); conn.close()
        await query.edit_message_text(query.message.text + "\n\n✅ تم التأكيد.")
        await context.bot.send_message(chat_id=data[1], text="✅ تم تأكيد طلبك وإضافته للدين. صحة وهنا!")

async def post_init(application: Application):
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد ☕"), BotCommand("ledger", "سجل ديوني 📋")], scope=BotCommandScopeDefault())
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد ☕"), BotCommand("ledger_admin", "دفتر الدين 📔"), BotCommand("history", "سجل الأيام السابقة 📅"), BotCommand("ledger", "سجل ديوني 📋")], scope=BotCommandScopeChat(chat_id=CASHIER_ID))

def main():
    init_db(); app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('start', start), CallbackQueryHandler(settle_start, pattern="^settle_")],
        states={CHOOSING_CATEGORY: [CallbackQueryHandler(category_choice)], CHOOSING_SERVICE: [CallbackQueryHandler(service_choice)],
                CONFIRMING_CART: [CallbackQueryHandler(confirm_cart)], LOCATION_TYPE: [CallbackQueryHandler(location_choice)],
                OFFICE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_office)],
                SETTLING_DEBT: [MessageHandler(filters.PHOTO | filters.Document.ALL, receive_debt_receipt)]},
        fallbacks=[CommandHandler('start', start)]))
    app.add_handler(CommandHandler('ledger_admin', admin_ledger))
    app.add_handler(CommandHandler('ledger', user_ledger))
    app.add_handler(CommandHandler('history', history_start))
    app.add_handler(CallbackQueryHandler(history_day_details, pattern="^hdate_"))
    app.add_handler(CallbackQueryHandler(send_reminder, pattern="^remind_"))
    app.add_handler(CallbackQueryHandler(clear_debt, pattern="^clear_"))
    app.add_handler(CallbackQueryHandler(cashier_action, pattern="^(conf|out)_"))
    
    print(f"Running Webhook on {PORT}")
    app.run_webhook(listen="0.0.0.0", port=PORT, secret_token="SecretPassword123", webhook_url=f"{WEBHOOK_DOMAIN}", drop_pending_updates=True)

if __name__ == '__main__': main()
