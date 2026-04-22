import psycopg2
import logging
import asyncio
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes

# --- الإعدادات ---
TOKEN = '8705243157:AAEvgDT3PecE8fmwc962NnToHnJl2xpFhAQ'
CASHIER_ID = 7447129659 
OLD_CASHIER_ID = 5312266808 

DATABASE_URL = os.environ.get('DATABASE_URL')
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

ASK_OFFICE, CHOOSING_CATEGORY, CHOOSING_SERVICE, CONFIRMING_CART, LOCATION_TYPE = range(5)
SETTLING_DEBT, PAY_AMOUNT, PAY_RECEIPT = 20, 30, 31

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection(); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id SERIAL PRIMARY KEY, user_id BIGINT,
                 details TEXT, total_price INTEGER, location TEXT, timestamp TEXT, 
                 status TEXT, is_paid INTEGER DEFAULT 0)''')
    conn.commit(); conn.close()

# --- مسار الطلب ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cart'] = []
    context.user_data.pop('editing_order_id', None)
    text = "يسعد أوقاتك! ☕\nللاستمتاع بتجربة صحيحة، يرجى كتابة **رقم مكتبك**:"
    if update.message: await update.message.reply_text(text, parse_mode='Markdown')
    return ASK_OFFICE

async def save_office_and_show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['office'] = f"مكتب {update.message.text}"
    return await show_categories(update, context)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("☕ مشروبات ساخنة", callback_data='cat_hot')],
                [InlineKeyboardButton("🥤 مشروبات باردة", callback_data='cat_cold')],
                [InlineKeyboardButton("🥪 سندويشات (بالمكان فقط)", callback_data='cat_sandwiches')],
                [InlineKeyboardButton("🍫 شوكلاتة", callback_data='cat_choc')],
                [InlineKeyboardButton("🍟 شبسي", callback_data='cat_chips')]]
    text = 'تفضل اختار القسم:'
    query = update.callback_query
    if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
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
    keyboard.append([InlineKeyboardButton("✅ تأكيد الطلب وإرساله", callback_data='confirm_order')])
    await query.edit_message_text(text=f"🛒 سلتك الحالية:\n{cart_list}\n\n💰 المجموع: {total} شيكل", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRMING_CART

async def confirm_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data == 'add_more': return await show_categories(update, context)
    elif query.data.startswith('remove_'):
        idx = int(query.data.split('_')[1]); context.user_data['cart'].pop(idx)
        return await service_choice(update, context)
    
    has_sandwich = any("سندويش" in item for item in context.user_data.get('cart', []))
    office = context.user_data.get('office', 'مكتبك')
    keyboard = [[InlineKeyboardButton(f"توصيل لـ {office} 🖥️", callback_data='مكتب')], [InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]]
    if has_sandwich: keyboard = [[InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]]
    await query.edit_message_text(text="وين حابب تستلم؟", reply_markup=InlineKeyboardMarkup(keyboard))
    return LOCATION_TYPE

async def location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    delivery_loc = context.user_data.get('office', 'مكتب غير معروف') if query.data == 'مكتب' else "حجز بالمكان"
    user = query.from_user
    cart = context.user_data['cart']; details = ", ".join(cart); total = sum(PRICES[item] for item in cart)
    
    conn = get_db_connection(); c = conn.cursor()
    editing_id = context.user_data.get('editing_order_id')
    if editing_id:
        c.execute("UPDATE orders SET details=%s, total_price=%s, location=%s, status='انتظار' WHERE id=%s", (details, total, delivery_loc, editing_id))
        order_id = editing_id
        context.user_data.pop('editing_order_id')
    else:
        c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                  (user.id, details, total, delivery_loc, datetime.now().strftime("%Y-%m-%d %H:%M"), "انتظار", 0))
        order_id = c.fetchone()[0]
    conn.commit(); conn.close()
    
    keyboard_cashier = [[InlineKeyboardButton("✅ تأكيد", callback_data=f"conf_{user.id}_{order_id}")],
                        [InlineKeyboardButton("⚠️ صنف ناقص", callback_data=f"out_{user.id}_{order_id}")]]
    await context.bot.send_message(chat_id=CASHIER_ID, text=f"🚨 **طلب #{order_id}**\n👤 {user.first_name}\n📦 {details}\n📍 {delivery_loc}\n💰 {total} ش", reply_markup=InlineKeyboardMarkup(keyboard_cashier))
    await query.edit_message_text(f"تم إرسال طلبك المحدث #{order_id} للكاشير. ⏳")
    return ConversationHandler.END

# ------------------------------------------------------------------
# 2. ميزة دفع فاتورة فورية (تحديث النص بناءً على طلب حاتم)
# ------------------------------------------------------------------
async def start_instant_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "كم قيمة الفاتورة اللي حابب تدفعها؟ هذه المعلومة مهمة للحسابات داخليا. اكتب الرقم مثلا (15)"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
    return PAY_AMOUNT

async def get_pay_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount_val = update.message.text
    context.user_data['pay_amount'] = amount_val
    text = f"تمام. ارفع الإشعار بعد اذنك بالمبلغ ({amount_val})."
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
    return PAY_RECEIPT

async def get_pay_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user; amt = int(context.user_data.get('pay_amount', 0))
    conn = get_db_connection(); c = conn.cursor()
    c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s)", (user.id, "فاتورة فورية", amt, "دفع فوري", datetime.now().strftime("%Y-%m-%d %H:%M"), "مقبول", 1))
    conn.commit(); conn.close()
    await update.message.reply_text("شكراً لك! تم التسجيل بنجاح. 🌸")
    await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id, caption=f"💰 فاتورة فورية: {amt} ش")
    return ConversationHandler.END

# ------------------------------------------------------------------
# 3. تعديل صفحة "ديوني" (تنسيق حاتم الجديد)
# ------------------------------------------------------------------
async def user_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT id, timestamp, total_price, details FROM orders WHERE user_id=%s AND is_paid=0 ORDER BY id DESC", (user_id,))
    rows = c.fetchall(); conn.close()
    
    if not rows: 
        return await update.message.reply_text("سجلك نظيف! ما عليك أي ديون حالياً. ✨")
    
    grand_total = sum(r[2] for r in rows)
    
    report = f"🔴 **قيمة الديون المتراكمة: {grand_total} شيكل**\n"
    report += "⎯" * 15 + "\n\n"
    
    for r in rows:
        # تنسيق التاريخ ليظهر اليوم والوقت بشكل أبسط
        dt_str = r[1] # "2026-04-22 19:10"
        report += f"📅 {dt_str} | 💰 {r[2]} ش\n"
        report += f"📦 {r[3]}\n"
        report += "⎯" * 10 + "\n"
        
    await update.message.reply_text(report)

# ------------------------------------------------------------------
# 4. معالجة الحالات المتقدمة (صنف ناقص، تعديل)
# ------------------------------------------------------------------
async def cashier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    conn = get_db_connection(); c = conn.cursor()
    if data[0] == "conf":
        c.execute("UPDATE orders SET status='مقبول' WHERE id=%s", (data[2],))
        await query.edit_message_text(query.message.text + "\n\n✅ تم التأكيد.")
        await context.bot.send_message(chat_id=data[1], text="✅ تم تأكيد طلبك. صحة وهنا!")
    elif data[0] == "out":
        user_id, order_id = data[1], data[2]
        c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
        items = [it.strip() for it in c.fetchone()[0].split(",")]
        keyboard = [[InlineKeyboardButton(f"❌ {it} غير متوفر", callback_data=f"rmv_{user_id}_{order_id}_{i}")] for i, it in enumerate(items)]
        await query.edit_message_text(f"اختار الصنف الناقص في طلب #{order_id}:", reply_markup=InlineKeyboardMarkup(keyboard))
    conn.commit(); conn.close()

async def remove_item_from_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    user_id, order_id, item_idx = data[1], int(data[2]), int(data[3])
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
    items = [it.strip() for it in c.fetchone()[0].split(",")]
    removed_item = items.pop(item_idx)
    new_details = ", ".join(items); new_total = sum(PRICES.get(it, 0) for it in items)
    c.execute("UPDATE orders SET details=%s, total_price=%s, status='تعديل زبون' WHERE id=%s", (new_details, new_total, order_id))
    conn.commit(); conn.close()
    await query.edit_message_text(f"⚠️ تم إبلاغ الزبون بنقص ({removed_item}).")
    keyboard = [[InlineKeyboardButton("➕ إضافة أصناف بديلة", callback_data=f"editback_{order_id}")],
                [InlineKeyboardButton("✅ إرسال الطلب المتبقي", callback_data=f"editready_{order_id}")]]
    await context.bot.send_message(chat_id=user_id, text=f"⚠️ عذراً، ({removed_item}) غير متوفر.\nسلتك الحالية: {new_details}\n\nحابب تضيف بديل ولا نعتمد هيك؟", reply_markup=InlineKeyboardMarkup(keyboard))

async def customer_handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    action, order_id = data[0], int(data[1])
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT details, location FROM orders WHERE id=%s", (order_id,))
    res = c.fetchone(); conn.close()
    context.user_data['cart'] = [it.strip() for it in res[0].split(",")] if res[0] else []
    context.user_data['editing_order_id'] = order_id
    if action == "editback": return await show_categories(update, context)
    else:
        keyboard = [[InlineKeyboardButton("توصيل للمكتب 🖥️", callback_data='مكتب')], [InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]]
        await query.edit_message_text("تأكيد مكان الاستلام:", reply_markup=InlineKeyboardMarkup(keyboard))
        return LOCATION_TYPE

# --- الإدارة ---
async def admin_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID: return
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE timestamp >= %s AND status != 'ملغي'", ((datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),))
    res = c.fetchone()
    c.execute("SELECT user_id, location, SUM(total_price) FROM orders WHERE is_paid=0 GROUP BY user_id, location")
    debtors = c.fetchall(); conn.close()
    keyboard = [[InlineKeyboardButton(f"🔔 {d[1]} ({d[2]} ش)", callback_data=f"remind_{d[0]}_{d[2]}")] for d in debtors]
    await update.message.reply_text(f"📔 الحسابات:\n📦 طلبات: {res[0] or 0}\n💰 مبيعات: {res[1] or 0} ش", reply_markup=InlineKeyboardMarkup(keyboard))

async def clear_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); user_id = query.data.split("_")[1]
    conn = get_db_connection(); c = conn.cursor(); c.execute("UPDATE orders SET is_paid=1 WHERE user_id=%s AND is_paid=0", (user_id,)); conn.commit(); conn.close()
    await query.edit_message_caption(caption="✅ تم التصفير."); await context.bot.send_message(chat_id=user_id, text="✅ تم تصفير حسابك.")

async def cancel_pay_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); await query.edit_message_text("✅ تم الإلغاء."); return ConversationHandler.END

async def post_init(application: Application):
    try: await application.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=OLD_CASHIER_ID))
    except: pass
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد ☕"), BotCommand("pay", "دفع فاتورة فورية 💳"), BotCommand("ledger", "سجل ديوني 📋")], scope=BotCommandScopeDefault())
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد ☕"), BotCommand("pay", "دفع فاتورة فورية 💳"), BotCommand("ledger_admin", "دفتر الحسابات 📔"), BotCommand("ledger", "سجل ديوني 📋")], scope=BotCommandScopeChat(chat_id=CASHIER_ID))

def main():
    init_db(); app = Application.builder().token(TOKEN).post_init(post_init).build()
    order_conv = ConversationHandler(
        entry_points=[CommandHandler('start', start), CallbackQueryHandler(customer_handle_edit, pattern="^edit(back|ready)_")],
        states={ASK_OFFICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_office_and_show_menu)], CHOOSING_CATEGORY: [CallbackQueryHandler(category_choice)], CHOOSING_SERVICE: [CallbackQueryHandler(service_choice)], CONFIRMING_CART: [CallbackQueryHandler(confirm_cart)], LOCATION_TYPE: [CallbackQueryHandler(location_choice)]},
        fallbacks=[CommandHandler('start', start)])
    app.add_handler(order_conv)
    app.add_handler(ConversationHandler(entry_points=[CommandHandler('pay', start_instant_pay)], states={PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pay_amount), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")], PAY_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.ALL, get_pay_receipt), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")]}, fallbacks=[CommandHandler('pay', start_instant_pay)]))
    app.add_handler(CallbackQueryHandler(remove_item_from_order, pattern="^rmv_"))
    app.add_handler(CallbackQueryHandler(cashier_action, pattern="^(conf|out)_"))
    app.add_handler(CommandHandler('ledger_admin', admin_ledger)); app.add_handler(CommandHandler('ledger', user_ledger)); app.add_handler(CallbackQueryHandler(clear_debt, pattern="^clear_"))
    app.run_webhook(listen="0.0.0.0", port=PORT, secret_token="SecretPassword123", webhook_url=f"{WEBHOOK_DOMAIN}", drop_pending_updates=True)

if __name__ == '__main__': main()
