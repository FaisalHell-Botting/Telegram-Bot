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
OLD_CASHIER_ID = 5312266808 # الـ ID تبعك القديم عشان ننظفه

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
    text = "يسعد أوقاتك! ☕\nللاستمتاع بتجربة صحيحة للبوت، يرجى كتابة **رقم مكتبك** الخاص في المساحة:"
    await update.message.reply_text(text, parse_mode='Markdown')
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
    text = 'تفضل اختار القسم اللي حابب تطلب منه:'
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
    office = context.user_data.get('office', 'مكتبك')
    
    if has_sandwich: 
        keyboard = [[InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]]
        text = "طلبك فيه سندويشات.. الاستلام فقط داخل (الكفي كورنر) نورتنا! ❤️"
    else:
        keyboard = [[InlineKeyboardButton(f"توصيل لـ {office} 🖥️", callback_data='مكتب')], 
                    [InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]]
        text = "وين حابب تستلم الطلب؟"
        
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    return LOCATION_TYPE

async def location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    delivery_loc = context.user_data['office'] if query.data == 'مكتب' else "حجز بالمكان"
    user = query.from_user
    cart = context.user_data['cart']; details = ", ".join(cart); total = sum(PRICES[item] for item in cart)
    
    conn = get_db_connection(); c = conn.cursor()
    c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
              (user.id, details, total, delivery_loc, datetime.now().strftime("%Y-%m-%d %H:%M"), "انتظار", 0))
    order_id = c.fetchone()[0]; conn.commit(); conn.close()
    
    keyboard_cashier = [[InlineKeyboardButton("✅ تأكيد وإضافة للدين", callback_data=f"conf_{user.id}_{order_id}")],
                        [InlineKeyboardButton("⚠️ صنف ناقص", callback_data=f"out_{user.id}_{order_id}")]]
    cashier_msg = await context.bot.send_message(chat_id=CASHIER_ID, text=f"🚨 **طلب #{order_id}**\n👤 {user.first_name}\n📦 {details}\n📍 {delivery_loc}\n💰 {total} ش", reply_markup=InlineKeyboardMarkup(keyboard_cashier))
    
    context.user_data[f'cashier_msg_{order_id}'] = cashier_msg.message_id
    keyboard_user = [[InlineKeyboardButton("❌ إلغاء الطلب", callback_data=f"cancel_{order_id}")]]
    await query.edit_message_text("تم الإرسال للكاشير. ⏳", reply_markup=InlineKeyboardMarkup(keyboard_user))
    return ConversationHandler.END

async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    order_id = query.data.split("_")[1]
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT status FROM orders WHERE id=%s", (order_id,))
    res = c.fetchone()
    if res and res[0] != 'مقبول':
        c.execute("UPDATE orders SET status='ملغي' WHERE id=%s", (order_id,))
        await query.edit_message_text("✅ تم إلغاء الطلب بنجاح.")
        msg_id = context.user_data.get(f'cashier_msg_{order_id}')
        if msg_id:
            try: await context.bot.edit_message_text(chat_id=CASHIER_ID, message_id=msg_id, text=f"🚫 **تم إلغاء الطلب #{order_id} من قبل الزبون.**")
            except: pass
    else:
        await query.edit_message_text("عذراً، لا يمكن الإلغاء لأن الطلب قيد التجهيز. 👨‍🍳")
    conn.commit(); conn.close()

# --- دفع فاتورة فورية ---
async def start_instant_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "قديش قيمة فاتورتك عشان الحسابات عنا؟ 📊"
    keyboard = [[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return PAY_AMOUNT

async def get_pay_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pay_amount'] = update.message.text
    text = f"حوّل مبلغ **{update.message.text} شيكل** للمحفظة:\n`0597489605` (كمال عبيد)\nوارفع الإيصال هان 👇"
    keyboard = [[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    return PAY_RECEIPT

async def get_pay_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    amount = int(context.user_data.get('pay_amount', 0))
    conn = get_db_connection(); c = conn.cursor()
    c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s)",
              (user.id, "فاتورة فورية", amount, "دفع فوري", datetime.now().strftime("%Y-%m-%d %H:%M"), "مقبول", 1))
    conn.commit(); conn.close()
    await update.message.reply_text("شكراً لك! تم التسجيل بنجاح. 🌸")
    caption = f"💰 **دفع فاتورة فورية!**\n👤 {user.first_name}\n💵 المبلغ: {amount} ش"
    if update.message.photo: await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id, caption=caption)
    return ConversationHandler.END

async def cancel_pay_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("✅ تم إلغاء العملية.")
    return ConversationHandler.END

# --- الإدارة والديون ---
async def user_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT id, timestamp, total_price, details FROM orders WHERE user_id=%s AND is_paid=0 ORDER BY id DESC", (user_id,))
    rows = c.fetchall(); conn.close()
    if not rows: return await update.message.reply_text("سجلك نظيف! ✨")
    report = "📋 **سجل ديونك:**\n" + "".join([f"🆔 #{r[0]} | 💰 {r[2]} ش\n📦 {r[3]}\n" for r in rows])
    await update.message.reply_text(report + f"\n🔴 المجموع: {sum(r[2] for r in rows)} ش")

async def admin_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID: return
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE timestamp >= %s AND status != 'ملغي'", (week_ago,))
    r1 = c.fetchone()
    c.execute("SELECT SUM(total_price) FROM orders WHERE timestamp >= %s AND location='دفع فوري'", (week_ago,))
    r2 = c.fetchone()
    c.execute("SELECT SUM(total_price) FROM orders WHERE timestamp >= %s AND is_paid=0", (week_ago,))
    r3 = c.fetchone()
    c.execute("SELECT user_id, location, SUM(total_price) FROM orders WHERE is_paid=0 GROUP BY user_id, location")
    debtors = c.fetchall(); conn.close()
    text = f"📔 **دفتر الحسابات:**\n📦 الطلبات: {r1[0] or 0}\n💰 المبيعات: {r1[1] or 0} ش\n⚡ فوري: {r2[0] or 0} ش\n🔴 ديون: {r3[0] or 0} ش"
    keyboard = [[InlineKeyboardButton(f"🔔 تذكير {d[1]} ({d[2]} ش)", callback_data=f"remind_{d[0]}_{d[2]}")] for d in debtors]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# --- معالجة أزرار الكاشير (تم حل مشكلة الزر الناقص هان) ---
async def cashier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    conn = get_db_connection(); c = conn.cursor()
    
    if data[0] == "conf": # تأكيد الطلب
        c.execute("SELECT status FROM orders WHERE id=%s", (data[2],))
        if c.fetchone()[0] == 'ملغي':
            await query.edit_message_text(query.message.text + "\n\n🚫 الزبون ألغى الطلب.")
        else:
            c.execute("UPDATE orders SET status='مقبول' WHERE id=%s", (data[2],))
            await query.edit_message_text(query.message.text + "\n\n✅ تم التأكيد.")
            await context.bot.send_message(chat_id=data[1], text="✅ تم تأكيد طلبك. صحة وهنا!")
            
    elif data[0] == "out": # صنف غير موجود (الحل الجديد)
        c.execute("UPDATE orders SET status='صنف ناقص' WHERE id=%s", (data[2],))
        await query.edit_message_text(query.message.text + "\n\n⚠️ تم إبلاغ الزبون بالنقص.")
        await context.bot.send_message(chat_id=data[1], text="⚠️ نعتذر منك، الصنف اللي طلبته غير متوفر حالياً.")
        
    conn.commit(); conn.close()

async def clear_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    user_id = query.data.split("_")[1]
    conn = get_db_connection(); c = conn.cursor()
    c.execute("UPDATE orders SET is_paid=1 WHERE user_id=%s AND is_paid=0", (user_id,))
    conn.commit(); conn.close()
    await query.edit_message_caption(caption="✅ تم تصفير حساب المكتب.")
    await context.bot.send_message(chat_id=user_id, text="✅ تم تأكيد استلام المبلغ وتصفير حسابك.")

async def send_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    _, uid, amt = query.data.split("_")
    keyboard = [[InlineKeyboardButton("💳 تسديد الآن", callback_data=f"settle_{amt}")]]
    try:
        await context.bot.send_message(chat_id=uid, text=f"🔔 تذكير: عليك مستحقات بقيمة {amt} شيكل.", reply_markup=InlineKeyboardMarkup(keyboard))
        await query.edit_message_text(query.message.text + "\n✅ تم التذكير.")
    except: await query.answer("تعذر الإرسال")

async def settle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    context.user_data['settle_amount'] = query.data.split("_")[1]
    await query.edit_message_text(f"تمام، المبلغ {context.user_data['settle_amount']} ش. ارفع الإيصال هان 👇")
    return SETTLING_DEBT

async def receive_debt_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await update.message.reply_text("وصل للكاشير للتأكيد. ❤️")
    keyboard = [[InlineKeyboardButton("✅ تأكيد وتصفير", callback_data=f"clear_{user.id}")]]
    caption = f"💰 تسديد دين من {user.first_name} بقيمة {context.user_data.get('settle_amount')} ش"
    await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id, caption=caption, reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

# --- إعداد القوائم ---
async def post_init(application: Application):
    # تنظيف المنيو من عند الـ ID القديم (حاتم)
    try: await application.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=OLD_CASHIER_ID))
    except: pass
    
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد"), BotCommand("pay", "دفع فوري"), BotCommand("ledger", "ديوني")], scope=BotCommandScopeDefault())
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد"), BotCommand("pay", "دفع فوري"), BotCommand("ledger_admin", "الحسابات"), BotCommand("ledger", "ديوني")], scope=BotCommandScopeChat(chat_id=CASHIER_ID))

def main():
    init_db(); app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(ConversationHandler(entry_points=[CommandHandler('start', start)], states={ASK_OFFICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_office_and_show_menu)], CHOOSING_CATEGORY: [CallbackQueryHandler(category_choice)], CHOOSING_SERVICE: [CallbackQueryHandler(service_choice)], CONFIRMING_CART: [CallbackQueryHandler(confirm_cart)], LOCATION_TYPE: [CallbackQueryHandler(location_choice)]}, fallbacks=[CommandHandler('start', start)]))
    app.add_handler(ConversationHandler(entry_points=[CommandHandler('pay', start_instant_pay)], states={PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pay_amount), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")], PAY_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.ALL, get_pay_receipt), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")]}, fallbacks=[CommandHandler('pay', start_instant_pay)]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(settle_start, pattern="^settle_")], states={SETTLING_DEBT: [MessageHandler(filters.PHOTO | filters.Document.ALL, receive_debt_receipt)]}, fallbacks=[CommandHandler('start', start)]))
    app.add_handler(CommandHandler('ledger_admin', admin_ledger)); app.add_handler(CommandHandler('ledger', user_ledger))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern="^cancel_")); app.add_handler(CallbackQueryHandler(send_reminder, pattern="^remind_")); app.add_handler(CallbackQueryHandler(clear_debt, pattern="^clear_")); app.add_handler(CallbackQueryHandler(cashier_action, pattern="^(conf|out)_"))
    app.run_webhook(listen="0.0.0.0", port=PORT, secret_token="SecretPassword123", webhook_url=f"{WEBHOOK_DOMAIN}", drop_pending_updates=True)

if __name__ == '__main__': main()
