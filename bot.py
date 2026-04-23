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
TOKEN = os.environ.get('BOT_TOKEN', '8705243157:AAEvgDT3PecE8fmwc962NnToHnJl2xpFhAQ')
CASHIER_ID = int(os.environ.get('CASHIER_ID', 7447129659))
OLD_CASHIER_ID = 5312266808

DATABASE_URL = os.environ.get('DATABASE_URL')
WEBHOOK_DOMAIN = "https://lego-food-bot.onrender.com"
PORT = int(os.environ.get('PORT', 8443))

WALLET_NUMBER = os.environ.get('WALLET_NUMBER', '0597489605')

PRICES = {
    'شاي': 1, 'قهوة مزاج وسط': 2, 'قهوة مزاج كبير': 3, 'نسكافيه مكس': 2, 'كفي مكس': 2, 'كابتشينو جوداي': 3,
    'كوكاكولا': 4, 'بلو أزرق': 4, 'مراعي حليب شوكلاتة': 2, 'عصير كوكتيل فواكه': 2, 'لتر عصير برتقال': 7, 'لتر عصير مانجا': 7,
    'سندويش فينو فيتا': 3, 'سندويش فينو مرتديلا': 3,
    'سنيكرز': 3, 'تويكس': 3, 'مارس': 3, 'مستر بايت': 4, 'قسماط حجم وسط': 4, 'بسكويت مالح': 2,
    'بسكويت ديمة فانيلا': 2, 'مولتو ميني': 2, 'شكلاتة تجارية ب 2شيكل': 2, 'شكلاتة تجارية ب 1 شيكل': 1, 'حلو نعنع سكوتش': 1,
    'برنجلز أحمر صغير': 6, 'برنجلز أحمر كبير': 11, 'برنجلز أحمر كبير شطة': 11, 'كيك فراولة': 7
}

ASK_OFFICE, CHOOSING_CATEGORY, CHOOSING_SERVICE, CONFIRMING_CART, LOCATION_TYPE = range(5)
PAY_AMOUNT, PAY_RECEIPT, SETTLING_DEBT = 30, 31, 32

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

db_pool = None

def init_db():
    global db_pool
    db_pool = pool.SimpleConnectionPool(1, 20, DATABASE_URL)
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS orders
                     (id SERIAL PRIMARY KEY, user_id BIGINT,
                     details TEXT, total_price INTEGER, location TEXT, timestamp TEXT,
                     status TEXT, is_paid INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ratings
                     (id SERIAL PRIMARY KEY, user_id BIGINT, order_id INTEGER,
                     rating INTEGER, timestamp TEXT)''')
        conn.commit()
        c.close()

@contextmanager
def get_db():
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)

def get_pal_time():
    return (datetime.utcnow() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")

async def cleanup_old_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_msg_id = context.user_data.get('last_msg_id')
    if old_msg_id:
        chat_id = update.effective_chat.id
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=old_msg_id,
                text="🚫 تم إلغاء هذه العملية لبدء أمر جديد.", reply_markup=None)
        except Exception:
            pass

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_old_message(update, context)
    if update.message:
        await update.message.reply_text("✅ تم إلغاء العملية الحالية بنجاح. البوت جاهز لخدمتك.")
    context.user_data.clear()
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_old_message(update, context)
    context.user_data.clear()
    context.user_data['cart'] = []
    text = "يسعد أوقاتك! ☕\nللاستمتاع بتجربة صحيحة، يرجى كتابة **رقم مكتبك**:"
    if update.message:
        msg = await update.message.reply_text(text, parse_mode='Markdown')
        context.user_data['last_msg_id'] = msg.message_id
    return ASK_OFFICE

async def save_office_and_show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['office'] = f"مكتب {update.message.text}"
    return await show_categories(update, context)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("☕ مشروبات ساخنة", callback_data='cat_hot')],
        [InlineKeyboardButton("🥤 مشروبات باردة", callback_data='cat_cold')],
        [InlineKeyboardButton("🥪 سندويشات (في الكوفي كورنر فقط)", callback_data='cat_sandwiches')],
        [InlineKeyboardButton("🍫 شوكلاتة", callback_data='cat_choc')],
        [InlineKeyboardButton("🍟 شبسي", callback_data='cat_chips')]
    ]
    text = 'تفضل اختار القسم:'
    query = update.callback_query
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['last_msg_id'] = query.message.message_id
    else:
        msg = await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['last_msg_id'] = msg.message_id
    return CHOOSING_CATEGORY

async def category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
    context.user_data['last_msg_id'] = query.message.message_id
    return CHOOSING_SERVICE

async def service_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
    await query.edit_message_text(text=f"🛒 سلتك الحالية:\n{cart_list}\n\n💰 المجموع: {total} شيكل",
        reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data['last_msg_id'] = query.message.message_id
    return CONFIRMING_CART

async def confirm_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'add_more':
        return await show_categories(update, context)
    elif query.data == 'cancel_order':
        await query.edit_message_text("✅ تم إلغاء الطلب. بإمكانك طلب شيء آخر في أي وقت.")
        context.user_data.clear()
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
        await query.edit_message_text(text=f"🛒 سلتك الحالية:\n{cart_list}\n\n💰 المجموع: {total} شيكل",
            reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['last_msg_id'] = query.message.message_id
        return CONFIRMING_CART
    elif query.data == 'confirm_order':
        has_sandwich = any("سندويش" in item for item in context.user_data.get('cart', []))
        office = context.user_data.get('office', 'مكتبك')
        keyboard = [
            [InlineKeyboardButton(f"توصيل لـ {office} 🖥️", callback_data='loc_office')],
            [InlineKeyboardButton("في الكوفي كورنر 🪑", callback_data='loc_place')]
        ]
        if has_sandwich:
            keyboard = [[InlineKeyboardButton("في الكوفي كورنر 🪑", callback_data='loc_place')]]
        await query.edit_message_text(text="وين حابب تستلم؟", reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['last_msg_id'] = query.message.message_id
        return LOCATION_TYPE

async def location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    office = context.user_data.get('office', 'مكتب غير معروف')
    user = query.from_user
    cart = context.user_data.get('cart', [])
    details = ", ".join(cart)
    total = sum(PRICES[item] for item in cart)
    db_location = office
    delivery_type = "توصيل" if query.data == 'loc_office' else "في الكوفي كورنر"
    with get_db() as conn:
        c = conn.cursor()
        editing_id = context.user_data.get('editing_order_id')
        if editing_id:
            c.execute("UPDATE orders SET details=%s, total_price=%s, location=%s, status='انتظار' WHERE id=%s",
                      (details, total, db_location, editing_id))
            order_id = editing_id
        else:
            c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                      (user.id, details, total, db_location, get_pal_time(), "انتظار", 0))
            order_id = c.fetchone()[0]
        conn.commit()
        c.close()
    keyboard_cashier = [
        [InlineKeyboardButton("✅ تأكيد", callback_data=f"conf_{user.id}_{order_id}")],
        [InlineKeyboardButton("⚠️ صنف ناقص", callback_data=f"out_{user.id}_{order_id}")]
    ]
    cashier_msg = await context.bot.send_message(chat_id=CASHIER_ID,
        text=f"🚨 **طلب #{order_id}**\n👤 {user.first_name}\n📦 {details}\n📍 {office} ({delivery_type})\n💰 {total} ش",
        reply_markup=InlineKeyboardMarkup(keyboard_cashier), parse_mode='Markdown')
    keyboard_user = [[InlineKeyboardButton("❌ التراجع وإلغاء الطلب", callback_data=f"usercancel_{order_id}_{cashier_msg.message_id}")]]
    await query.edit_message_text(
        f"تم إرسال طلبك #{order_id} للكاشير. ⏳\n\nفي حال أخطأت أو غيرت رأيك، يمكنك التراجع عنه قبل تأكيد الكاشير:",
        reply_markup=InlineKeyboardMarkup(keyboard_user))
    context.application.bot_data[f'wait_msg_{order_id}'] = query.message.message_id
    context.user_data.clear()
    return ConversationHandler.END

async def user_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
                await context.bot.edit_message_text(chat_id=CASHIER_ID, message_id=cashier_msg_id,
                    text=f"🚫 الطلب #{order_id} تم إلغاؤه من قبل الزبون.")
            except Exception:
                pass
        else:
            await query.edit_message_text(
                f"⚠️ لا يمكنك إلغاء الطلب #{order_id} لأنه قيد التحضير وتم قبوله أو تعديله من الكاشير.")
        c.close()

async def start_instant_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_old_message(update, context)
    context.user_data.clear()
    text = "كم قيمة المشتريات اللي حابب تدفعها؟ هذه المعلومة مهمة للحسابات داخليا. اكتب الرقم مثلا (15)"
    msg = await update.message.reply_text(text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
    context.user_data['last_msg_id'] = msg.message_id
    return PAY_AMOUNT

async def get_pay_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount_val = update.message.text.strip()
    chat_id = update.effective_chat.id
    old_msg_id = context.user_data.get('last_msg_id')
    if old_msg_id:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
        except Exception: pass
    try: await update.message.delete()
    except Exception: pass
    if not amount_val.isdigit():
        msg = await context.bot.send_message(chat_id=chat_id,
            text="⚠️ خطأ: الرجاء كتابة أرقام فقط (مثال: 15). حاول مجدداً:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
        context.user_data['last_msg_id'] = msg.message_id
        return PAY_AMOUNT
    context.user_data['pay_amount'] = amount_val
    text = (f"تمام. ارفع الإشعار بعد اذنك بالمبلغ ({amount_val}) شيكل.\n\n"
            f"🔹 للتحويل عبر محفظة بال باي (كمال عبيد):\n`{WALLET_NUMBER}`\n\n"
            f"🔹 أو عبر بنك فلسطين (محمد جندية):\n"
            f"رقم الحساب: `1512081`\n"
            f"آيبان: `PS11PALS045115120810993100000`\n\n"
            f"(اضغط على أي رقم لنسخه)")
    keyboard = [
        [InlineKeyboardButton("✏️ تعديل الرقم", callback_data="editpay")],
        [InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]
    ]
    msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data['last_msg_id'] = msg.message_id
    return PAY_RECEIPT

async def edit_pay_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "كم قيمة المشتريات اللي حابب تدفعها؟ هذه المعلومة مهمة للحسابات داخليا. اكتب الرقم مثلا (15)"
    await query.edit_message_text(text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
    return PAY_AMOUNT

async def get_pay_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("⚠️ الرجاء إرسال صورة الإيصال فقط، وليس ملفاً.")
        return PAY_RECEIPT
    old_msg_id = context.user_data.get('last_msg_id')
    if old_msg_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_msg_id)
        except Exception: pass
    user = update.message.from_user
    amt = int(context.user_data.get('pay_amount', 0))
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (user.id, "شراء من الكوفي كورنر", amt, "دفع فوري", get_pal_time(), "مقبول", 1))
        conn.commit()
        c.close()
    await update.message.reply_text("شكراً لك! تم التسجيل بنجاح. 🌸")
    await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id,
        caption=f"💰 شراء مباشر: {amt} ش\nمن: {user.first_name}")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_pay_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ تم الإلغاء.")
    context.user_data.clear()
    return ConversationHandler.END

async def settle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_old_message(update, context)
    context.user_data.clear()
    query = update.callback_query
    await query.answer()
    context.user_data['settle_amount'] = query.data.split("_")[1]
    text = (f"تمام، لتسديد مبلغ ({context.user_data['settle_amount']}) شيكل، ارفع إيصال التحويل هان 👇\n\n"
            f"🔹 للتحويل عبر محفظة بال باي (كمال عبيد):\n`{WALLET_NUMBER}`\n\n"
            f"🔹 أو عبر بنك فلسطين (محمد جندية):\n"
            f"رقم الحساب: `1512081`\n"
            f"آيبان: `PS11PALS045115120810993100000`\n\n"
            f"(اضغط على أي رقم لنسخه)")
    msg = await query.edit_message_text(text, parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancelpay")]]))
    context.user_data['last_msg_id'] = msg.message_id
    return SETTLING_DEBT

async def receive_debt_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("⚠️ الرجاء إرسال صورة الإيصال فقط، وليس ملفاً.")
        return SETTLING_DEBT
    old_msg_id = context.user_data.get('last_msg_id')
    if old_msg_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_msg_id)
        except Exception: pass
    user = update.message.from_user
    await update.message.reply_text("وصل للكاشير للتأكيد وتم إرسال طلب التصفير. ❤️")
    keyboard = [[InlineKeyboardButton("✅ تأكيد وتصفير الحساب", callback_data=f"clear_{user.id}")]]
    caption = f"💰 طلب تسديد دين!\n👤 من: {user.first_name}\n💵 المبلغ: {context.user_data.get('settle_amount')} ش"
    await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id,
        caption=caption, reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.clear()
    return ConversationHandler.END

async def user_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, timestamp, total_price, details FROM orders WHERE user_id=%s AND is_paid=0 AND status='مقبول' ORDER BY id DESC", (user_id,))
        rows = c.fetchall()
        c.close()
    if not rows:
        return await update.message.reply_text("سجلك نظيف! ما عليك أي ديون حالياً. ✨")
    grand_total = sum(r[2] for r in rows)
    report = f"🔴 **قيمة الديون المتراكمة: {grand_total} شيكل**\n" + "⎯" * 15 + "\n\n"
    for r in rows:
        report += f"📅 {r[1]} | 💰 {r[2]} ش\n📦 {r[3]}\n" + "⎯" * 10 + "\n"
    await update.message.reply_text(report, parse_mode='Markdown')

# ------------------------------------------------------------------
# ===== نظام التقييم (مع تأخير 10 دقائق) =====
# ------------------------------------------------------------------

async def _send_rating_job(context: ContextTypes.DEFAULT_TYPE):
    """
    مهمة مجدولة — تُرسَل بعد 10 دقائق من تأكيد الكاشير للطلب.
    تحتاج: pip install 'python-telegram-bot[job-queue]'
    """
    user_id  = context.job.data['user_id']
    order_id = context.job.data['order_id']
    keyboard = [
        [InlineKeyboardButton("🌟 ممتاز وسريع، رائع!!", callback_data=f"rate_5_{order_id}")],
        [InlineKeyboardButton("😊 كويس",               callback_data=f"rate_4_{order_id}")],
        [InlineKeyboardButton("😐 عادي",               callback_data=f"rate_3_{order_id}")],
        [InlineKeyboardButton("🐢 بطيء",               callback_data=f"rate_2_{order_id}")],
        [InlineKeyboardButton("😤 بصراحة مش نافع",    callback_data=f"rate_1_{order_id}")]
    ]
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"⭐ كيف كانت تجربتك في هذه العملية؟\nطلب #{order_id}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.warning(f"تعذر إرسال طلب التقييم للمستخدم {user_id}: {e}")

def schedule_rating_request(context: ContextTypes.DEFAULT_TYPE, user_id: int, order_id: int, delay_minutes: int = 10):
    """جدولة رسالة التقييم بعد delay_minutes دقيقة"""
    if context.application.job_queue is None:
        logger.warning("job_queue غير مفعّل — لن تُرسَل رسالة التقييم. ثبّت: pip install 'python-telegram-bot[job-queue]'")
        return
    context.application.job_queue.run_once(
        _send_rating_job,
        when=delay_minutes * 60,
        data={'user_id': user_id, 'order_id': order_id},
        name=f"rating_{order_id}"
    )

async def handle_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة تقييم الزبون"""
    query = update.callback_query
    await query.answer()
    parts      = query.data.split("_")
    rating_val = int(parts[1])
    order_id   = int(parts[2])
    user_id    = query.from_user.id
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM ratings WHERE user_id=%s AND order_id=%s", (user_id, order_id))
        if c.fetchone():
            await query.edit_message_text("✅ سبق وسجلت تقييمك لهذا الطلب. شكراً!")
            c.close()
            return
        c.execute("INSERT INTO ratings (user_id, order_id, rating, timestamp) VALUES (%s, %s, %s, %s)",
            (user_id, order_id, rating_val, get_pal_time()))
        conn.commit()
        c.close()
    rating_labels = {5: "🌟 ممتاز وسريع، رائع!!", 4: "😊 كويس", 3: "😐 عادي", 2: "🐢 بطيء", 1: "😤 بصراحة مش نافع"}
    await query.edit_message_text(
        f"شكراً على تقييمك! ❤️\nاخترت: {rating_labels.get(rating_val, '')}\nرأيك مهم لتحسين الخدمة. 🌸")

async def admin_ratings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /ratings — إحصائيات التقييمات للكاشير فقط"""
    if update.message.chat_id != CASHIER_ID:
        return
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*), AVG(rating) FROM ratings")
        res = c.fetchone()
        total_ratings = res[0] or 0
        avg_rating    = res[1]
        c.close()
    if total_ratings == 0:
        await update.message.reply_text("⭐ لا يوجد تقييمات حتى الآن.")
        return
    avg_str = f"{avg_rating:.1f}" if avg_rating else "—"
    stars   = "⭐" * round(avg_rating) if avg_rating else ""
    report = (
        f"📊 **إحصائيات التقييمات:**\n\n"
        f"✨ متوسط التقييم: {avg_str} / 5  {stars}\n"
        f"📝 عدد التقييمات: {total_ratings}"
    )
    await update.message.reply_text(report, parse_mode='Markdown')

# ------------------------------------------------------------------
# الكاشير والزبون — معالجة الطلبات
# ------------------------------------------------------------------

async def cashier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != CASHIER_ID:
        await query.answer("⛔ غير مصرح لك بهذا الإجراء.", show_alert=True)
        return
    data = query.data.split("_")
    with get_db() as conn:
        c = conn.cursor()
        if data[0] == "conf":
            user_id, order_id = int(data[1]), int(data[2])
            c.execute("UPDATE orders SET status='مقبول' WHERE id=%s", (order_id,))
            c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
            details = c.fetchone()[0]
            conn.commit()
            await query.edit_message_text(query.message.text + "\n\n✅ تم التأكيد.")
            user_final_text = f"✅ **تم تأكيد طلبك!**\n📦 {details}\n📝 تم إضافة الطلب على دفتر الدين.\nصحة وهنا! ❤️"
            wait_msg_id = context.application.bot_data.get(f'wait_msg_{order_id}')
            if wait_msg_id:
                try:
                    await context.bot.edit_message_text(chat_id=user_id, message_id=wait_msg_id,
                        text=user_final_text, parse_mode='Markdown')
                except Exception:
                    await context.bot.send_message(chat_id=user_id, text=user_final_text, parse_mode='Markdown')
            else:
                await context.bot.send_message(chat_id=user_id, text=user_final_text, parse_mode='Markdown')
            # جدولة رسالة التقييم بعد 10 دقائق
            schedule_rating_request(context, user_id, order_id, delay_minutes=10)
        elif data[0] == "out":
            user_id, order_id = data[1], data[2]
            c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
            items = [it.strip() for it in c.fetchone()[0].split(",")]
            keyboard = [[InlineKeyboardButton(f"❌ {it} غير متوفر", callback_data=f"rmv_{user_id}_{order_id}_{i}")] for i, it in enumerate(items)]
            await query.edit_message_text(f"اختار الصنف الناقص في طلب #{order_id}:", reply_markup=InlineKeyboardMarkup(keyboard))
        c.close()

async def remove_item_from_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != CASHIER_ID:
        await query.answer("⛔ غير مصرح لك بهذا الإجراء.", show_alert=True)
        return
    data = query.data.split("_")
    user_id, order_id, item_idx = int(data[1]), int(data[2]), int(data[3])
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
        items = [it.strip() for it in c.fetchone()[0].split(",")]
        removed_item = items.pop(item_idx)
        new_details = ", ".join(items)
        new_total = sum(PRICES.get(it, 0) for it in items)
        c.execute("UPDATE orders SET details=%s, total_price=%s, status='تعديل زبون' WHERE id=%s",
                  (new_details, new_total, order_id))
        conn.commit()
        c.close()
    await query.edit_message_text(f"⚠️ تم إبلاغ الزبون بنقص ({removed_item}).")
    wait_msg_id = context.application.bot_data.get(f'wait_msg_{order_id}')
    if wait_msg_id:
        try: await context.bot.delete_message(chat_id=user_id, message_id=wait_msg_id)
        except Exception: pass
    keyboard = [
        [InlineKeyboardButton("➕ إضافة أصناف بديلة", callback_data=f"editback_{order_id}")],
        [InlineKeyboardButton("✅ إرسال الطلب المتبقي", callback_data=f"editready_{order_id}")]
    ]
    await context.bot.send_message(chat_id=user_id,
        text=f"⚠️ عذراً، ({removed_item}) غير متوفر.\nسلتك الحالية: {new_details}\n\nحابب تضيف بديل ولا نعتمد هيك؟",
        reply_markup=InlineKeyboardMarkup(keyboard))

async def customer_handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action, order_id = data[0], int(data[1])
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT details, location FROM orders WHERE id=%s", (order_id,))
        res = c.fetchone()
        c.close()
    context.user_data['cart'] = [it.strip() for it in res[0].split(",")] if res[0] else []
    context.user_data['editing_order_id'] = order_id
    context.user_data['last_msg_id'] = query.message.message_id
    if res[1] and "مكتب" in res[1]:
        context.user_data['office'] = res[1]
    if action == "editback":
        return await show_categories(update, context)
    else:
        keyboard = [
            [InlineKeyboardButton("توصيل للمكتب 🖥️", callback_data='loc_office')],
            [InlineKeyboardButton("في الكوفي كورنر 🪑", callback_data='loc_place')]
        ]
        await query.edit_message_text("تأكيد مكان الاستلام:", reply_markup=InlineKeyboardMarkup(keyboard))
        return LOCATION_TYPE

async def admin_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID:
        return
    week_ago = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=7)).strftime("%Y-%m-%d")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE timestamp >= %s AND status = 'مقبول'", (week_ago,))
        res_total = c.fetchone()
        total_orders = res_total[0] or 0
        total_sales  = res_total[1] or 0
        c.execute("SELECT SUM(total_price) FROM orders WHERE timestamp >= %s AND location = 'دفع فوري'", (week_ago,))
        instant_paid = c.fetchone()[0] or 0
        c.execute("SELECT SUM(total_price) FROM orders WHERE timestamp >= %s AND is_paid = 0 AND status = 'مقبول' AND location != 'دفع فوري'", (week_ago,))
        total_debts = c.fetchone()[0] or 0
        c.execute("SELECT user_id, location, SUM(total_price) FROM orders WHERE is_paid = 0 AND status = 'مقبول' AND location != 'دفع فوري' GROUP BY user_id, location")
        debtors = c.fetchall()
        c.close()
    report = (f"📔 **دفتر الحسابات (آخر 7 أيام):**\n"
              f"📦 إجمالي الطلبات: {total_orders} حركة\n"
              f"💰 المبيعات الكلية: {total_sales} شيكل\n"
              f"⚡ إجمالي الدفع الفوري: {instant_paid} شيكل\n"
              f"🔴 إجمالي الديون المعلقة: {total_debts} شيكل\n\n")
    keyboard = [[InlineKeyboardButton(f"🔔 {d[1]} ({d[2]} ش)", callback_data=f"remind_{d[0]}_{d[2]}")] for d in debtors]
    if not keyboard:
        report += "✨ لا يوجد ديون معلقة حالياً."
    await update.message.reply_text(report, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def send_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != CASHIER_ID:
        await query.answer("⛔ غير مصرح لك بهذا الإجراء.", show_alert=True)
        return
    _, uid, amt = query.data.split("_")
    keyboard = [[InlineKeyboardButton("💳 تسديد الآن", callback_data=f"settle_{amt}")]]
    try:
        await context.bot.send_message(chat_id=uid,
            text=f"🔔 تذكير لطيف: تفضل بتسديد مستحقات الكوفي كورنر بقيمة {amt} شيكل لضمان استمرار الخدمة.",
            reply_markup=InlineKeyboardMarkup(keyboard))
        await query.answer("✅ تم إرسال التذكير بنجاح للموظف!", show_alert=True)
    except Exception:
        await query.answer("⚠️ تعذر الإرسال، يبدو أن المستخدم قام بحظر البوت.", show_alert=True)

async def clear_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != CASHIER_ID:
        await query.answer("⛔ غير مصرح لك بهذا الإجراء.", show_alert=True)
        return
    user_id = query.data.split("_")[1]
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE orders SET is_paid=1 WHERE user_id=%s AND is_paid=0", (user_id,))
        conn.commit()
        c.close()
    try:
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ تم تأكيد استلام المبلغ وتصفير الحساب.")
    except Exception:
        pass
    await context.bot.send_message(chat_id=user_id, text="✅ تم تأكيد استلام المبلغ وتصفير حسابك بنجاح. شكراً لك!")

# --- بدء البوت ---
async def post_init(application: Application):
    try:
        await application.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=OLD_CASHIER_ID))
    except Exception:
        pass
    await application.bot.set_my_commands([
        BotCommand("start",  "طلب جديد ☕"),
        BotCommand("pay",    "شراء من داخل الكفي كورنر 💳"),
        BotCommand("ledger", "سجل ديوني 📋"),
        BotCommand("cancel", "إلغاء العملية الحالية ❌")
    ], scope=BotCommandScopeDefault())
    await application.bot.set_my_commands([
        BotCommand("start",        "طلب جديد ☕"),
        BotCommand("pay",          "شراء من داخل الكفي كورنر 💳"),
        BotCommand("ledger_admin", "دفتر الحسابات 📔"),
        BotCommand("ratings",      "إحصائيات التقييمات ⭐"),
        BotCommand("ledger",       "سجل ديوني 📋")
    ], scope=BotCommandScopeChat(chat_id=CASHIER_ID))

def main():
    init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    if app.job_queue is None:
        logger.warning(
            "job_queue غير مفعّل — رسائل التقييم لن تُرسَل بعد 10 دقائق. "
            "ثبّت: pip install 'python-telegram-bot[job-queue]'"
        )

    main_conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('pay', start_instant_pay),
            CallbackQueryHandler(customer_handle_edit, pattern="^edit(back|ready)_"),
            CallbackQueryHandler(settle_start, pattern="^settle_")
        ],
        states={
            ASK_OFFICE:        [MessageHandler(filters.TEXT & ~filters.COMMAND, save_office_and_show_menu)],
            CHOOSING_CATEGORY: [CallbackQueryHandler(category_choice, pattern="^cat_")],
            CHOOSING_SERVICE:  [CallbackQueryHandler(service_choice,  pattern="^(item_|back_to_main$)")],
            CONFIRMING_CART:   [CallbackQueryHandler(confirm_cart,    pattern=r"^(remove_\d+|add_more|confirm_order|cancel_order)$")],
            LOCATION_TYPE:     [CallbackQueryHandler(location_choice, pattern="^loc_")],
            PAY_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_pay_amount),
                CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")
            ],
            PAY_RECEIPT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, get_pay_receipt),
                CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$"),
                CallbackQueryHandler(edit_pay_amount, pattern="^editpay$")
            ],
            SETTLING_DEBT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, receive_debt_receipt),
                CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")
            ]
        },
        fallbacks=[
            CommandHandler('start',  start),
            CommandHandler('pay',    start_instant_pay),
            CommandHandler('cancel', cancel_command)
        ]
    )
    app.add_handler(main_conv)
    app.add_handler(CallbackQueryHandler(user_cancel_order,      pattern="^usercancel_"))
    app.add_handler(CallbackQueryHandler(remove_item_from_order, pattern="^rmv_"))
    app.add_handler(CallbackQueryHandler(cashier_action,         pattern="^(conf|out)_"))
    app.add_handler(CallbackQueryHandler(handle_rating,          pattern="^rate_"))
    app.add_handler(CommandHandler('ledger_admin', admin_ledger))
    app.add_handler(CommandHandler('ledger',       user_ledger))
    app.add_handler(CommandHandler('ratings',      admin_ratings))
    app.add_handler(CallbackQueryHandler(send_reminder, pattern="^remind_"))
    app.add_handler(CallbackQueryHandler(clear_debt,    pattern="^clear_"))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        secret_token="SecretPassword123",
        webhook_url=WEBHOOK_DOMAIN,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
