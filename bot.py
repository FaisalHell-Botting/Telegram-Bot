import psycopg2
from psycopg2 import pool
import logging
import asyncio
import os
from contextlib import contextmanager
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

# --- 1. مدير الاتصالات (Connection Pool) لتحسين الأداء وحماية الداتا بيز ---
db_pool = None

def init_db():
    global db_pool
    # إنشاء حوض اتصالات يتحمل من 1 إلى 20 اتصال متزامن
    db_pool = pool.SimpleConnectionPool(1, 20, DATABASE_URL)
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS orders
                     (id SERIAL PRIMARY KEY, user_id BIGINT,
                     details TEXT, total_price INTEGER, location TEXT, timestamp TEXT, 
                     status TEXT, is_paid INTEGER DEFAULT 0)''')
        conn.commit()
        c.close()

@contextmanager
def get_db():
    # دالة ذكية لسحب وإرجاع الاتصال بأمان حتى لو حصل خطأ
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)

# --- 2. دالة توقيت فلسطين الدقيق ---
def get_pal_time():
    # إضافة 3 ساعات لتوقيت جرينتش ليطابق توقيت فلسطين
    return (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")

# --- أمر الإلغاء العام ---
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("✅ تم إلغاء العملية الحالية بنجاح. يمكنك بدء طلب جديد.")
    context.user_data.clear()
    return ConversationHandler.END

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
    keyboard = [[InlineKeyboardButton(f"{item} ({PRICES[item]} ش)", callback_data=f"item_{item}")] for item in items]
    keyboard.append([InlineKeyboardButton("⬅️ رجوع", callback_data='back_to_main')])
    await query.edit_message_text(text="اختار الصنف:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSING_SERVICE

async def service_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    
    if query.data == 'back_to_main': 
        return await show_categories(update, context)
    
    if query.data.startswith('item_'):
        item_name = query.data.replace('item_', '')
        context.user_data.setdefault('cart', []).append(item_name)
    
    total = sum(PRICES[item] for item in context.user_data['cart'])
    cart_list = "\n".join([f"• {item}" for item in context.user_data['cart']])
    
    keyboard = [[InlineKeyboardButton(f"❌ حذف {item}", callback_data=f'remove_{i}')] for i, item in enumerate(context.user_data['cart'])]
    keyboard.append([InlineKeyboardButton("➕ إضافة أصناف", callback_data='add_more')])
    keyboard.append([InlineKeyboardButton("✅ تأكيد الطلب وإرساله", callback_data='confirm_order')])
    keyboard.append([InlineKeyboardButton("🗑️ إلغاء الطلب بالكامل", callback_data='cancel_order')])
    
    await query.edit_message_text(text=f"🛒 سلتك الحالية:\n{cart_list}\n\n💰 المجموع: {total} شيكل", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRMING_CART

async def confirm_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    
    if query.data == 'add_more': 
        return await show_categories(update, context)
    
    elif query.data == 'cancel_order':
        context.user_data['cart'] = []
        await query.edit_message_text("✅ تم إلغاء الطلب. بإمكانك طلب شيء آخر في أي وقت.")
        return ConversationHandler.END

    elif query.data.startswith('remove_'):
        idx = int(query.data.split('_')[1])
        if 0 <= idx < len(context.user_data.get('cart', [])):
            context.user_data['cart'].pop(idx)
        
        if not context.user_data.get('cart'):
            await query.edit_message_text("سلتك فارغة الآن. الرجاء اختيار قسم من جديد:")
            return await show_categories(update, context)
            
        total = sum(PRICES[item] for item in context.user_data['cart'])
        cart_list = "\n".join([f"• {item}" for item in context.user_data['cart']])
        keyboard = [[InlineKeyboardButton(f"❌ حذف {item}", callback_data=f'remove_{i}')] for i, item in enumerate(context.user_data['cart'])]
        keyboard.append([InlineKeyboardButton("➕ إضافة أصناف", callback_data='add_more')])
        keyboard.append([InlineKeyboardButton("✅ تأكيد الطلب وإرساله", callback_data='confirm_order')])
        keyboard.append([InlineKeyboardButton("🗑️ إلغاء الطلب بالكامل", callback_data='cancel_order')])
        
        await query.edit_message_text(text=f"🛒 سلتك الحالية:\n{cart_list}\n\n💰 المجموع: {total} شيكل", reply_markup=InlineKeyboardMarkup(keyboard))
        return CONFIRMING_CART
        
    elif query.data == 'confirm_order':
        has_sandwich = any("سندويش" in item for item in context.user_data.get('cart', []))
        office = context.user_data.get('office', 'مكتبك')
        keyboard = [[InlineKeyboardButton(f"توصيل لـ {office} 🖥️", callback_data='loc_office')], [InlineKeyboardButton("حجز بالمكان 🪑", callback_data='loc_place')]]
        if has_sandwich: 
            keyboard = [[InlineKeyboardButton("حجز بالمكان 🪑", callback_data='loc_place')]]
        await query.edit_message_text(text="وين حابب تستلم؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return LOCATION_TYPE

async def location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    delivery_loc = context.user_data.get('office', 'مكتب غير معروف') if query.data == 'loc_office' else "حجز بالمكان"
    user = query.from_user
    cart = context.user_data.get('cart', [])
    details = ", ".join(cart); total = sum(PRICES[item] for item in cart)
    
    with get_db() as conn:
        c = conn.cursor()
        editing_id = context.user_data.get('editing_order_id')
        if editing_id:
            c.execute("UPDATE orders SET details=%s, total_price=%s, location=%s, status='انتظار' WHERE id=%s", (details, total, delivery_loc, editing_id))
            order_id = editing_id
            context.user_data.pop('editing_order_id', None)
        else:
            c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                      (user.id, details, total, delivery_loc, get_pal_time(), "انتظار", 0))
            order_id = c.fetchone()[0]
        conn.commit()
        c.close()
    
    keyboard_cashier = [[InlineKeyboardButton("✅ تأكيد", callback_data=f"conf_{user.id}_{order_id}")],
                        [InlineKeyboardButton("⚠️ صنف ناقص", callback_data=f"out_{user.id}_{order_id}")]]
    
    cashier_msg = await context.bot.send_message(chat_id=CASHIER_ID, text=f"🚨 **طلب #{order_id}**\n👤 {user.first_name}\n📦 {details}\n📍 {delivery_loc}\n💰 {total} ش", reply_markup=InlineKeyboardMarkup(keyboard_cashier))
    
    keyboard_user = [[InlineKeyboardButton("❌ التراجع وإلغاء الطلب", callback_data=f"usercancel_{order_id}_{cashier_msg.message_id}")]]
    await query.edit_message_text(f"تم إرسال طلبك المحدث #{order_id} للكاشير. ⏳\n\nفي حال أخطأت أو غيرت رأيك، يمكنك التراجع عنه قبل تأكيد الكاشير:", reply_markup=InlineKeyboardMarkup(keyboard_user))
    
    context.user_data['cart'] = []
    return ConversationHandler.END

# ------------------------------------------------------------------
# إلغاء الطلب من طرف الزبون
# ------------------------------------------------------------------
async def user_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data = query.data.split("_")
    order_id = int(data[1])
    cashier_msg_id = int(data[2])

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT status FROM orders WHERE id=%s", (order_id,))
        res = c.fetchone()

        if res and res[0] == "انتظار":
            c.execute("UPDATE orders SET status='ملغي' WHERE id=%s", (order_id,))
            conn.commit()
            await query.edit_message_text(f"✅ تم سحب وإلغاء الطلب #{order_id} بنجاح.")
            try:
                await context.bot.edit_message_text(chat_id=CASHIER_ID, message_id=cashier_msg_id, text=f"🚫 الطلب #{order_id} تم إلغاؤه من قبل الزبون.")
            except Exception: pass
        else:
            await query.edit_message_text(f"⚠️ لا يمكنك إلغاء الطلب #{order_id} لأنه قيد التحضير وتم قبوله أو تعديله من الكاشير.")
        c.close()

# ------------------------------------------------------------------
# ميزة دفع فاتورة فورية (مع إضافة التحقق من الأرقام - Validation)
# ------------------------------------------------------------------
async def start_instant_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "كم قيمة الفاتورة اللي حابب تدفعها؟ هذه المعلومة مهمة للحسابات داخليا. اكتب الرقم مثلا (15)"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
    return PAY_AMOUNT

async def get_pay_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount_val = update.message.text.strip()
    
    # 3. التحقق الذكي: هل ما أدخله المستخدم هو أرقام فقط؟
    if not amount_val.isdigit():
        await update.message.reply_text("⚠️ خطأ: الرجاء كتابة أرقام فقط (مثال: 15) بدون أي نصوص إضافية. حاول مجدداً:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
        return PAY_AMOUNT # البقاء في نفس الخطوة حتى يدخل الرقم الصحيح

    context.user_data['pay_amount'] = amount_val
    text = f"تمام. ارفع الإشعار بعد اذنك بالمبلغ ({amount_val})."
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
    return PAY_RECEIPT

async def get_pay_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user; amt = int(context.user_data.get('pay_amount', 0))
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s)", (user.id, "فاتورة فورية", amt, "دفع فوري", get_pal_time(), "مقبول", 1))
        conn.commit()
        c.close()
        
    await update.message.reply_text("شكراً لك! تم التسجيل بنجاح. 🌸")
    await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id, caption=f"💰 فاتورة فورية: {amt} ش\nمن: {user.first_name}")
    return ConversationHandler.END

async def cancel_pay_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("✅ تم الإلغاء.")
    return ConversationHandler.END

# ------------------------------------------------------------------
# صفحة "ديوني"
# ------------------------------------------------------------------
async def user_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, timestamp, total_price, details FROM orders WHERE user_id=%s AND is_paid=0 ORDER BY id DESC", (user_id,))
        rows = c.fetchall()
        c.close()
    
    if not rows: 
        return await update.message.reply_text("سجلك نظيف! ما عليك أي ديون حالياً. ✨")
    
    grand_total = sum(r[2] for r in rows)
    report = f"🔴 **قيمة الديون المتراكمة: {grand_total} شيكل**\n"
    report += "⎯" * 15 + "\n\n"
    
    for r in rows:
        dt_str = r[1]
        report += f"📅 {dt_str} | 💰 {r[2]} ش\n"
        report += f"📦 {r[3]}\n"
        report += "⎯" * 10 + "\n"
        
    await update.message.reply_text(report)

# ------------------------------------------------------------------
# معالجة الحالات المتقدمة (صنف ناقص، تعديل) للكاشير
# ------------------------------------------------------------------
async def cashier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    with get_db() as conn:
        c = conn.cursor()
        if data[0] == "conf":
            c.execute("UPDATE orders SET status='مقبول' WHERE id=%s", (data[2],))
            conn.commit()
            await query.edit_message_text(query.message.text + "\n\n✅ تم التأكيد.")
            await context.bot.send_message(chat_id=data[1], text="✅ تم تأكيد طلبك. صحة وهنا!")
        elif data[0] == "out":
            user_id, order_id = data[1], data[2]
            c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
            items = [it.strip() for it in c.fetchone()[0].split(",")]
            keyboard = [[InlineKeyboardButton(f"❌ {it} غير متوفر", callback_data=f"rmv_{user_id}_{order_id}_{i}")] for i, it in enumerate(items)]
            await query.edit_message_text(f"اختار الصنف الناقص في طلب #{order_id}:", reply_markup=InlineKeyboardMarkup(keyboard))
        c.close()

async def remove_item_from_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    user_id, order_id, item_idx = data[1], int(data[2]), int(data[3])
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
        items = [it.strip() for it in c.fetchone()[0].split(",")]
        removed_item = items.pop(item_idx)
        new_details = ", ".join(items); new_total = sum(PRICES.get(it, 0) for it in items)
        c.execute("UPDATE orders SET details=%s, total_price=%s, status='تعديل زبون' WHERE id=%s", (new_details, new_total, order_id))
        conn.commit()
        c.close()
        
    await query.edit_message_text(f"⚠️ تم إبلاغ الزبون بنقص ({removed_item}).")
    keyboard = [[InlineKeyboardButton("➕ إضافة أصناف بديلة", callback_data=f"editback_{order_id}")],
                [InlineKeyboardButton("✅ إرسال الطلب المتبقي", callback_data=f"editready_{order_id}")]]
    await context.bot.send_message(chat_id=user_id, text=f"⚠️ عذراً، ({removed_item}) غير متوفر.\nسلتك الحالية: {new_details}\n\nحابب تضيف بديل ولا نعتمد هيك؟", reply_markup=InlineKeyboardMarkup(keyboard))

async def customer_handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data.split("_")
    action, order_id = data[0], int(data[1])
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT details, location FROM orders WHERE id=%s", (order_id,))
        res = c.fetchone()
        c.close()
        
    context.user_data['cart'] = [it.strip() for it in res[0].split(",")] if res[0] else []
    context.user_data['editing_order_id'] = order_id
    if action == "editback": return await show_categories(update, context)
    else:
        keyboard = [[InlineKeyboardButton("توصيل للمكتب 🖥️", callback_data='loc_office')], [InlineKeyboardButton("حجز بالمكان 🪑", callback_data='loc_place')]]
        await query.edit_message_text("تأكيد مكان الاستلام:", reply_markup=InlineKeyboardMarkup(keyboard))
        return LOCATION_TYPE

# --- الإدارة ---
async def admin_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID: return
    # حساب توقيت فلسطين ناقص 7 أيام
    week_ago = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=7)).strftime("%Y-%m-%d")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE timestamp >= %s AND status != 'ملغي'", (week_ago,))
        res = c.fetchone()
        c.execute("SELECT user_id, location, SUM(total_price) FROM orders WHERE is_paid=0 GROUP BY user_id, location")
        debtors = c.fetchall()
        c.close()
        
    keyboard = [[InlineKeyboardButton(f"🔔 {d[1]} ({d[2]} ش)", callback_data=f"remind_{d[0]}_{d[2]}")] for d in debtors]
    await update.message.reply_text(f"📔 الحسابات:\n📦 طلبات: {res[0] or 0}\n💰 مبيعات: {res[1] or 0} ش", reply_markup=InlineKeyboardMarkup(keyboard))

async def clear_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); user_id = query.data.split("_")[1]
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE orders SET is_paid=1 WHERE user_id=%s AND is_paid=0", (user_id,))
        conn.commit()
        c.close()
    await query.edit_message_caption(caption="✅ تم التصفير."); await context.bot.send_message(chat_id=user_id, text="✅ تم تصفير حسابك.")

# --- بدء البوت ---
async def post_init(application: Application):
    try: await application.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=OLD_CASHIER_ID))
    except: pass
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد ☕"), BotCommand("pay", "دفع فاتورة فورية 💳"), BotCommand("ledger", "سجل ديوني 📋"), BotCommand("cancel", "إلغاء العملية الحالية ❌")], scope=BotCommandScopeDefault())
    await application.bot.set_my_commands([BotCommand("start", "طلب جديد ☕"), BotCommand("pay", "دفع فاتورة فورية 💳"), BotCommand("ledger_admin", "دفتر الحسابات 📔"), BotCommand("ledger", "سجل ديوني 📋")], scope=BotCommandScopeChat(chat_id=CASHIER_ID))

def main():
    init_db(); app = Application.builder().token(TOKEN).post_init(post_init).build()
    
    order_conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start), 
            CallbackQueryHandler(customer_handle_edit, pattern="^edit(back|ready)_")
        ],
        states={
            ASK_OFFICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_office_and_show_menu)], 
            CHOOSING_CATEGORY: [CallbackQueryHandler(category_choice, pattern="^cat_")], 
            CHOOSING_SERVICE: [CallbackQueryHandler(service_choice, pattern="^(item_|back_to_main$)")], 
            CONFIRMING_CART: [CallbackQueryHandler(confirm_cart, pattern="^(remove_\d+|add_more|confirm_order|cancel_order)$")], 
            LOCATION_TYPE: [CallbackQueryHandler(location_choice, pattern="^loc_")]
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel_command)])
    app.add_handler(order_conv)
    
    pay_conv = ConversationHandler(
        entry_points=[CommandHandler('pay', start_instant_pay)], 
        states={
            PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pay_amount), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")], 
            PAY_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.ALL, get_pay_receipt), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")]
        }, 
        fallbacks=[CommandHandler('pay', start_instant_pay), CommandHandler('cancel', cancel_command)])
    app.add_handler(pay_conv)
    
    app.add_handler(CallbackQueryHandler(user_cancel_order, pattern="^usercancel_"))
    app.add_handler(CallbackQueryHandler(remove_item_from_order, pattern="^rmv_"))
    app.add_handler(CallbackQueryHandler(cashier_action, pattern="^(conf|out)_"))
    app.add_handler(CommandHandler('ledger_admin', admin_ledger)); app.add_handler(CommandHandler('ledger', user_ledger)); app.add_handler(CallbackQueryHandler(clear_debt, pattern="^clear_"))
    
    app.run_webhook(listen="0.0.0.0", port=PORT, secret_token="SecretPassword123", webhook_url=f"{WEBHOOK_DOMAIN}", drop_pending_updates=True)

if __name__ == '__main__': main()
