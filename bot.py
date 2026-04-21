from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes

# --- الإعدادات ---
TOKEN = '8705243157:AAErlqBFgca2WbgpUAw0TaJulKXp6kV-W-8'
CASHIER_ID = 5312266808
DB_NAME = 'orders_v2.db'

# المنيو والأسعار بالشيكل
PRICES = {
    'شاي': 2, 'نسكافيه': 2, 'كبتشينو': 2, 'قهوة': 3,
    'كوكاكولا': 4, 'بلو': 4, 'سبرايت': 4, 'فانتا': 4, 'شويبس': 3,
    'زعتر صغير': 2, 'زعتر وسط': 4, 'زعتر كبير': 5, 'جبنة بالخضار': 3, 'جبنة مع زيتون': 4, 'فطيرة بالنوتيلا': 6,
    'تشيكن بيتزا': 12, 'فاهيتا': 8
}

# حالات المحادثة
CHOOSING_CATEGORY, CHOOSING_SERVICE, CONFIRMING_CART, LOCATION_TYPE, OFFICE_NUMBER, PAYMENT_PROOF = range(6)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- قاعدة البيانات والتنظيف ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
                  details TEXT, total_price INTEGER, location TEXT, timestamp TEXT, rating INTEGER)''')
    
    # تحديث الجدول القديم لو كان موجود عشان نضيف عمود التقييم
    try:
        c.execute("ALTER TABLE orders ADD COLUMN rating INTEGER")
    except sqlite3.OperationalError:
        pass # العمود موجود مسبقاً
        
    conn.commit()
    conn.close()

def clean_old_data():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM orders WHERE date(substr(timestamp, 1, 10)) <= date('now', '-30 days')")
    conn.commit()
    conn.close()

# --- وظائف مساعدة ---
async def render_cart(update_or_query, context):
    """دالة مساعدة لعرض السلة مع أزرار الحذف"""
    cart = context.user_data.get('cart', [])
    query = update_or_query.callback_query if hasattr(update_or_query, 'callback_query') else update_or_query
    
    if not cart:
        await query.answer("السلة فاضية هلقيت!")
        return await show_categories(update_or_query, context)

    total = sum(PRICES[item] for item in cart)
    cart_list = "\n".join([f"• {item}" for item in cart])
    
    keyboard = []
    # زر حذف لكل صنف
    for i, item in enumerate(cart):
        keyboard.append([InlineKeyboardButton(f"❌ إزالة ({item})", callback_data=f'remove_{i}')])
        
    keyboard.append([InlineKeyboardButton("➕ إضافة صنف آخر", callback_data='add_more')])
    keyboard.append([InlineKeyboardButton("✅ هاد طلبي (تأكيد ودفع)", callback_data='confirm_order')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"سلتك فيها هلقيت:\n{cart_list}\n\n💰 المجموع: {total} شيكل\n\nحابب تضيف شي تاني ولا نعتمد؟"
    
    await query.edit_message_text(text=text, reply_markup=reply_markup)
    return CONFIRMING_CART

# --- الوظائف الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cart'] = []
    return await show_categories(update, context)

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("☕ مشاريب سخنة", callback_data='cat_hot')],
        [InlineKeyboardButton("🥤 مشاريب باردة", callback_data='cat_cold')],
        [InlineKeyboardButton("🥐 معجنات", callback_data='cat_pastries')],
        [InlineKeyboardButton("🥪 سندويشات", callback_data='cat_sandwiches')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    # تخصيص دبلوماسي محايد للجنسين ومناسب لأهل غزة
    text = 'يسعد أوقاتك بكل خير! نورتنا بالمساحة ❤️\nشو حابب تطلب اليوم؟ تفضل من المنيو:'
    
    if hasattr(update, 'message') and update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    return CHOOSING_CATEGORY

async def category_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data
    
    menu = {
        'cat_hot': ['شاي', 'نسكافيه', 'كبتشينو', 'قهوة'],
        'cat_cold': ['كوكاكولا', 'بلو', 'سبرايت', 'فانتا', 'شويبس'],
        'cat_pastries': ['زعتر صغير', 'زعتر وسط', 'زعتر كبير', 'جبنة بالخضار', 'جبنة مع زيتون', 'فطيرة بالنوتيلا'],
        'cat_sandwiches': ['تشيكن بيتزا', 'فاهيتا']
    }
    
    keyboard = [[InlineKeyboardButton(f"{item} ({PRICES[item]} شيكل)", callback_data=item)] for item in menu[cat]]
    keyboard.append([InlineKeyboardButton("⬅️ رجوع للأقسام", callback_data='back_to_main')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="تفضل، اختار الصنف اللي بدك اياه:", reply_markup=reply_markup)
    return CHOOSING_SERVICE

async def service_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'back_to_main':
        return await show_categories(update, context)
        
    selected_item = query.data
    context.user_data['cart'].append(selected_item)
    
    return await render_cart(update, context)

async def confirm_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'add_more':
        return await show_categories(update, context)
        
    elif query.data.startswith('remove_'):
        idx = int(query.data.split('_')[1])
        context.user_data['cart'].pop(idx)
        return await render_cart(update, context)
        
    keyboard = [
        [InlineKeyboardButton("ع المكتب 🖥️", callback_data='مكتب')],
        [InlineKeyboardButton("حجز بالمكان 🪑", callback_data='حجز مكان')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="عالعين والراس، وين حابب تستلم الطلب؟", reply_markup=reply_markup)
    return LOCATION_TYPE

async def location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['location_type'] = query.data
    
    if query.data == 'مكتب':
        await query.edit_message_text(text="تمام، اكتبلي هلقيت رقم مكتبك:")
        return OFFICE_NUMBER
    else:
        context.user_data['office'] = "حجز بالمكان"
        await query.edit_message_text(text="ولا يهمك، هلقيت ابعتلي صورة إشعار الدفع (سكرين شوت) عشان نعتمد الطلب:")
        return PAYMENT_PROOF

async def get_office(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['office'] = f"مكتب {update.message.text}"
    await update.message.reply_text("سجلنا الرقم.. هلقيت صورلي إشعار الدفع وابعت الصورة:")
    return PAYMENT_PROOF

async def get_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        file_id = None
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith('image/'):
            file_id = update.message.document.file_id
            
        if not file_id:
            await update.message.reply_text("عذراً، لازم تبعت إشعار الدفع كصورة عشان نعتمد الطلب. جرب ابعتها كمان مرة:")
            return PAYMENT_PROOF

        cart = context.user_data.get('cart', [])
        details = ", ".join(cart)
        total_price = sum(PRICES[item] for item in cart)
        office = context.user_data.get('office', 'غير محدد')
        user_id = update.message.from_user.id
        user_name = update.message.from_user.first_name

        clean_old_data()

        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (user_id, details, total_price, office, datetime.now().strftime("%Y-%m-%d %H:%M")))
        order_id = c.lastrowid # نحفظ رقم الطلب عشان نستخدمه بالتقييم
        conn.commit()
        conn.close()

        # إرسال للكاشير مع تضمين رقم الطلب في الزر
        keyboard = [[InlineKeyboardButton("✅ تأكيد وجاري التجهيز", callback_data=f"confirm_{user_id}_{order_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        caption = (f"🚨 **طلب جديد!**\n\n"
                   f"👤 الزبون: {user_name}\n"
                   f"📦 الطلبات:\n- " + "\n- ".join(cart) + 
                   f"\n\n💰 الإجمالي: {total_price} شيكل\n"
                   f"📍 المكان: {office}")

        await context.bot.send_photo(
            chat_id=CASHIER_ID, 
            photo=file_id, 
            caption=caption, 
            reply_markup=reply_markup,
            read_timeout=60, write_timeout=60, connect_timeout=60
        )
        
        # التعديل الجديد على رسالة الانتظار
        await update.message.reply_text("يسلموا إيديك، طلبك صار عند الكاشير وهلقيت براجع الطلب ليجهزلك اياه. ثواني وبجيك رسالة تأكيد على حالة الطلب! ⏳")
        return ConversationHandler.END

    except Exception as e:
        logging.error(f"خطأ: {e}")
        await update.message.reply_text("صار مشكلة فنية صغيرة، معلش جرب تبعت الصورة كمان مرة.")
        return PAYMENT_PROOF

# --- وظيفة التقييم المؤجلة ---
async def delayed_rating_prompt(bot, chat_id, order_id):
    # للتيست: غير الرقم 600 لـ 10 (عشان يبعتلك بعد 10 ثواني وتتأكد انه شغال)
    # 600 ثانية = 10 دقائق
    await asyncio.sleep(600) 
    
    keyboard = [
        [InlineKeyboardButton("😍 ممتاز وخدمة سريعة", callback_data=f"rate_5_{order_id}")],
        [InlineKeyboardButton("🙂 منيح", callback_data=f"rate_4_{order_id}")],
        [InlineKeyboardButton("😕 يعني مش كتير", callback_data=f"rate_2_{order_id}")],
        [InlineKeyboardButton("😞 بصراحة ما عجبني", callback_data=f"rate_1_{order_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="وصلك الطلب؟ صحة وهنا! ☕\nطمنا كيف كان طلبك اليوم وقيّم الخدمة:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logging.error(f"Failed to send rating prompt: {e}")

# --- استجابات الأزرار للكاشير والتقييم ---
async def cashier_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("confirm_"):
        data_parts = query.data.split("_")
        user_to_notify = data_parts[1]
        order_id = data_parts[2]
        
        # جلب بيانات الطلب لعمل بطاقة الملخص
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT details, total_price, location FROM orders WHERE id=?", (order_id,))
        order = c.fetchone()
        conn.close()
        
        if order:
            tracking_msg = f"🧾 **ملخص طلبك:**\n" \
                           f"📦 **الأصناف:** {order[0]}\n" \
                           f"💰 **الإجمالي:** {order[1]} شيكل\n" \
                           f"📍 **المكان:** {order[2]}\n" \
                           f"⏳ **الحالة:** 👨‍🍳 جاري التجهيز وهيكون في الطريق إلك كمان شوية. صحة وهنا!"
            try:
                await context.bot.send_message(chat_id=user_to_notify, text=tracking_msg)
                await query.edit_message_caption(caption=query.message.caption + "\n\n✅ **تم التأكيد والزبون تبلغ.**")
                
                # تفعيل مؤقت التقييم (بعد 10 دقائق)
                context.application.create_task(delayed_rating_prompt(context.bot, user_to_notify, order_id))
            except Exception as e:
                logging.error(f"Error notifying user: {e}")

    elif query.data.startswith("rep_"):
        date_str = query.data.split("_")[1]
        from bot import build_report_for_date # تجنب التكرار
        report_text = build_report_for_date(date_str)
        await query.edit_message_text(report_text)

async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    score = int(data[1])
    order_id = data[2]

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE orders SET rating = ? WHERE id = ?", (score, order_id))
    conn.commit()
    conn.close()

    await query.edit_message_text(text="✅ اعتمدنا التقييم. شكراً إلك وملاحظاتك دائماً بتهمنا!")

# --- تقارير الغلة والتقييم ---
def build_report_for_date(target_date):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT location, COUNT(*), SUM(total_price) FROM orders WHERE timestamp LIKE ? GROUP BY location", (f"{target_date}%",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return f"ما في غلة مسجلة لتاريخ {target_date}."

    total_orders = sum(row[1] for row in rows)
    total_cash = sum(row[2] for row in rows)

    report = f"📊 **تقرير الغلة لتاريخ ({target_date}):**\n\n"
    report += f"📦 إجمالي الطلبات الكلي: {total_orders}\n"
    report += f"💰 التكلفة الكلية لليوم: {total_cash} شيكل\n"
    report += "➖" * 12 + "\n"
    
    for row in rows:
        report += f"📍 {row[0]} | {row[1]} طلبات | {row[2]} شيكل\n"
    return report

async def daily_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID: return
    today = datetime.now().strftime("%Y-%m-%d")
    await update.message.reply_text(build_report_for_date(today))

async def history_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != CASHIER_ID: return
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT DISTINCT substr(timestamp, 1, 10) FROM orders ORDER BY timestamp DESC LIMIT 30")
    dates = c.fetchall()
    conn.close()

    if not dates:
        return await update.message.reply_text("لسه ما في أي أيام مسجلة في الدفتر.")

    keyboard = [[InlineKeyboardButton(f"غلة يوم {d[0]}", callback_data=f"rep_{d[0]}")] for d in dates]
    await update.message.reply_text("📅 تفضل يا كاشير، اختار اليوم اللي بدك تشوف غلته:", reply_markup=InlineKeyboardMarkup(keyboard))

async def rating_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم الكاشير لمتابعة التقييمات"""
    if update.message.chat_id != CASHIER_ID: return
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT AVG(rating), COUNT(rating) FROM orders WHERE rating IS NOT NULL")
    avg_row = c.fetchone()
    avg_rating = round(avg_row[0], 1) if avg_row[0] else 0
    total_ratings = avg_row[1]

    c.execute("SELECT details, location, rating FROM orders WHERE rating IS NOT NULL ORDER BY id DESC LIMIT 10")
    recent = c.fetchall()
    conn.close()

    rep = f"⭐️ **التقييم العام للمساحة:** {avg_rating} / 5.0 (من {total_ratings} تقييم)\n\n"
    rep += "📋 **آخر التقييمات (أحدث 10):**\n"
    for r in recent:
        stars = "😍 5/5" if r[2]==5 else "🙂 4/5" if r[2]==4 else "😕 2/5" if r[2]==2 else "😞 1/5"
        rep += f"• طلب ({r[1]}) - {r[0]}: {stars}\n"

    await update.message.reply_text(rep)

# --- برمجة أزرار المنيو حسب الشخص ---
async def post_init(application: Application):
    await application.bot.set_my_commands(
        [BotCommand("start", "اطلب طلب جديد 🍽️")],
        scope=BotCommandScopeDefault()
    )
    await application.bot.set_my_commands(
        [
            BotCommand("start", "اطلب طلب جديد 🍽️"),
            BotCommand("report", "غلة اليوم 📊"),
            BotCommand("history", "سجل الغلة 📅"),
            BotCommand("rating", "متابعة التقييمات ⭐️")
        ],
        scope=BotCommandScopeChat(chat_id=CASHIER_ID)
    )

def main():
    init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING_CATEGORY: [CallbackQueryHandler(category_choice)],
            CHOOSING_SERVICE: [CallbackQueryHandler(service_choice)],
            CONFIRMING_CART: [CallbackQueryHandler(confirm_cart)],
            LOCATION_TYPE: [CallbackQueryHandler(location_choice)],
            OFFICE_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_office)],
            PAYMENT_PROOF: [MessageHandler(filters.ALL & ~filters.COMMAND, get_payment)],
        },
        fallbacks=[CommandHandler('start', start)],
        per_message=False,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler('report', daily_report))
    app.add_handler(CommandHandler('history', history_report))
    app.add_handler(CommandHandler('rating', rating_report)) # الأمر الجديد للكاشير
    app.add_handler(CallbackQueryHandler(cashier_callback, pattern="^(confirm_|rep_)"))
    app.add_handler(CallbackQueryHandler(rating_callback, pattern="^rate_")) # معالج التقييم

    print("البوت المطور شغال هلقيت بنظام التقييم وإدارة السلة.. توكلنا على الله")
    keep_alive()
    app.run_polling()

if __name__ == '__main__':
    main()