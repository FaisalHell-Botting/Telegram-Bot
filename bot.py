import psycopg2
from psycopg2 import pool
import logging
import asyncio
import os
import json
import random
import google.generativeai as genai
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

# إعداد مفاتيح Gemini (يدعم مفتاح واحد أو عدة مفاتيح مفصولة بفاصلة)
GEMINI_KEYS_ENV = os.environ.get('GEMINI_API_KEY', '')
GEMINI_KEYS = [k.strip() for k in GEMINI_KEYS_ENV.split(',')] if GEMINI_KEYS_ENV else []

PRICES = {
    'شاي': 1, 'قهوة مزاج وسط': 2, 'قهوة مزاج كبير': 3, 'نسكافيه مكس': 2, 'كفي مكس': 2, 'كابتشينو جوداي': 3,
    'كوكاكولا': 4, 'بلو أزرق': 4, 'مراعي حليب شوكلاتة': 2, 'عصير كوكتيل فواكه': 2, 'لتر عصير برتقال': 7, 'لتر عصير مانجا': 7,
    'سندويش فينو فيتا': 4, 'سندويش فينو مرتديلا': 5,
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

async def show_cart_ui(update: Update, context: ContextTypes.DEFAULT_TYPE, base_text=""):
    cart = context.user_data.get('cart', [])
    total = sum(PRICES[item] for item in cart)
    cart_list = "\n".join([f"• {item}" for item in cart])
    text = f"{base_text}\n\n🛒 سلتك الحالية:\n{cart_list}\n\n💰 المجموع: {total} شيكل"

    keyboard = [[InlineKeyboardButton(f"❌ حذف {item}", callback_data=f'remove_{i}')] for i, item in enumerate(cart)]
    keyboard.append([InlineKeyboardButton("➕ إضافة أصناف", callback_data='add_more')])
    keyboard.append([InlineKeyboardButton("✅ تأكيد الطلب وإرساله", callback_data='confirm_order')])
    keyboard.append([InlineKeyboardButton("🗑️ إلغاء الطلب بالكامل", callback_data='cancel_order')])

    if update.callback_query:
        await update.callback_query.edit_message_text(text=text.strip(), reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['last_msg_id'] = update.callback_query.message.message_id
    else:
        msg = await update.message.reply_text(text=text.strip(), reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['last_msg_id'] = msg.message_id
    return CONFIRMING_CART

# ------------------------------------------------------------------
# ===== ذكاء اصطناعي: التقاط الطلب الحر عبر Gemini مع تدوير المفاتيح =====
# ------------------------------------------------------------------
async def handle_ai_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    
    if len(user_text) < 3 or not GEMINI_KEYS:
        await unknown_text(update, context)
        return ConversationHandler.END

    wait_msg = await update.message.reply_text("🤖 جاري فهم طلبك...")

    try:
        # اختيار مفتاح عشوائي لتوزيع الحمل وتجنب الـ Limit
        genai.configure(api_key=random.choice(GEMINI_KEYS))
        
        model = genai.GenerativeModel('gemini-2.0-flash') # تم استخدام أحدث موديل مستقر
        prompt = f"""
        أنت كاشير ذكي لكوفي كورنر. استخرج الطلب من رسالة الزبون التالية: '{user_text}'
        قائمة الأصناف المتاحة حرفياً: {list(PRICES.keys())}
        القواعد:
        1. طابق الأصناف مع القائمة المتاحة فقط بناءً على أقرب معنى.
        2. إذا طلب كمية (مثلا 2 قهوة وسط)، كرر اسم الصنف في المصفوفة مرتين.
        3. إذا طلب شيئاً غير متوفر، ضعه في مصفوفة unmatched.
        4. استخرج رقم المكتب إذا ذكره الزبون (أرقام فقط)، وإلا اجعله null.
        يجب أن يكون الرد بصيغة JSON فقط بهذا الهيكل الدقيق:
        {{"office": "15", "items": ["شاي", "قهوة مزاج وسط", "قهوة مزاج وسط"], "unmatched": ["عصير رمان"]}}
        تحذير: لا تقم بإضافة أي نصوص أو علامات Markdown مثل ```json حول الرد.
        """
        
        response = await model.generate_content_async(prompt)
        clean_text = response.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(clean_text)

        items = data.get("items", [])
        unmatched = data.get("unmatched", [])
        office = data.get("office")

        await wait_msg.delete()

        if not items and not unmatched:
            await unknown_text(update, context)
            return ConversationHandler.END

        context.user_data['cart'] = items
        reply_text = ""
        
        if unmatched:
            reply_text += f"⚠️ المعذرة، هذه الأصناف غير متوفرة لدينا: {', '.join(unmatched)}\n"

        if items:
            if office:
                context.user_data['office'] = f"مكتب {office}"
                return await show_cart_ui(update, context, reply_text + "🤖 فهمت طلبك تماماً!")
            else:
                reply_text += "🤖 فهمت الأصناف المطلوبة، لكن **ما هو رقم مكتبك؟** الرجاء كتابته الآن:"
                msg = await update.message.reply_text(reply_text)
                context.user_data['last_msg_id'] = msg.message_id
                return ASK_OFFICE
        else:
            await update.message.reply_text(reply_text + "\nلم أجد أصنافاً متاحة في طلبك، يرجى المحاولة عبر القوائم.")
            return await show_categories(update, context)

    except Exception as e:
        error_msg = str(e).lower()
        await wait_msg.delete()
        
        # معالجة ذكية لتجاوز الحد المسموح (Quota Exceeded)
        if any(x in error_msg for x in ["429", "quota", "limit", "exhausted"]):
            await update.message.reply_text("🤖 المساعد الذكي مشغول حالياً! تفضل المنيو السريع لخدمتك فوراً 👇")
            return await show_categories(update, context)
        
        logger.error(f"Gemini Error: {e}")
        await update.message.reply_text("⚠️ المعذرة، لم أتمكن من معالجة الطلب نصياً. الرجاء استخدام الأزرار 👇")
        return await show_categories(update, context)

# --- بقية الدوال الأساسية للمحادثة ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_old_message(update, context)
    context.user_data.clear()
    context.user_data['cart'] = []
    text = "يسعد أوقاتك! ☕\nللاستمتاع بتجربة صحيحة، يرجى كتابة **رقم مكتبك**:\n\n*(أو يمكنك كتابة طلبك مباشرة، مثلاً: بدي 2 قهوة وسط لمكتب 15)*"
    if update.message:
        msg = await update.message.reply_text(text, parse_mode='Markdown')
        context.user_data['last_msg_id'] = msg.message_id
    return ASK_OFFICE

async def save_office_and_show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    
    if len(user_text) > 15 or "بدي" in user_text:
        return await handle_ai_order(update, context)

    if "منيو" in user_text:
        await update.message.reply_text("⚠️ الرجاء كتابة رقم أو اسم مكتب صحيح (مثال: 15):")
        return ASK_OFFICE

    context.user_data['office'] = f"مكتب {user_text}"
    if context.user_data.get('cart'):
        return await show_cart_ui(update, context, "✅ تم حفظ رقم المكتب بنجاح.")
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
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['last_msg_id'] = update.callback_query.message.message_id
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
    return await show_cart_ui(update, context)

async def confirm_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'add_more': return await show_categories(update, context)
    elif query.data == 'cancel_order':
        await query.edit_message_text("✅ تم إلغاء الطلب.")
        context.user_data.clear()
        return ConversationHandler.END
    elif query.data.startswith('remove_'):
        idx = int(query.data.split('_')[1])
        if 0 <= idx < len(context.user_data.get('cart', [])): context.user_data['cart'].pop(idx)
        if not context.user_data.get('cart'): return await show_categories(update, context)
        return await show_cart_ui(update, context)
    elif query.data == 'confirm_order':
        has_sandwich = any("سندويش" in item for item in context.user_data.get('cart', []))
        office = context.user_data.get('office', 'مكتبك')
        keyboard = [[InlineKeyboardButton(f"توصيل لـ {office} 🖥️", callback_data='loc_office')],
                    [InlineKeyboardButton("في الكوفي كورنر 🪑", callback_data='loc_place')]]
        if has_sandwich: keyboard = [[InlineKeyboardButton("في الكوفي كورنر 🪑", callback_data='loc_place')]]
        await query.edit_message_text(text="وين حابب تستلم؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return LOCATION_TYPE

async def location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    office = context.user_data.get('office', 'مكتب غير معروف')
    user = query.from_user
    cart = context.user_data.get('cart', [])
    details, total = ", ".join(cart), sum(PRICES[item] for item in cart)
    db_location = office
    delivery_type = "توصيل" if query.data == 'loc_office' else "في الكوفي كورنر"
    with get_db() as conn:
        c = conn.cursor()
        editing_id = context.user_data.get('editing_order_id')
        if editing_id:
            c.execute("UPDATE orders SET details=%s, total_price=%s, location=%s, status='انتظار' WHERE id=%s", (details, total, db_location, editing_id))
            order_id = editing_id
        else:
            c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id", (user.id, details, total, db_location, get_pal_time(), "انتظار", 0))
            order_id = c.fetchone()[0]
        conn.commit()
    keyboard_cashier = [[InlineKeyboardButton("✅ تأكيد", callback_data=f"conf_{user.id}_{order_id}")],
                        [InlineKeyboardButton("⚠️ صنف ناقص", callback_data=f"out_{user.id}_{order_id}")]]
    cashier_msg = await context.bot.send_message(chat_id=CASHIER_ID, text=f"🚨 **طلب #{order_id}**\n👤 {user.first_name}\n📦 {details}\n📍 {office} ({delivery_type})\n💰 {total} ش", reply_markup=InlineKeyboardMarkup(keyboard_cashier), parse_mode='Markdown')
    await query.edit_message_text(f"تم إرسال طلبك #{order_id} للكاشير. ⏳", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ التراجع وإلغاء الطلب", callback_data=f"usercancel_{order_id}_{cashier_msg.message_id}")]]))
    context.application.bot_data[f'wait_msg_{order_id}'] = query.message.message_id
    context.user_data.clear()
    return ConversationHandler.END

# --- دوال الدفع والديون (بدون تغيير) ---

async def start_instant_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_old_message(update, context); context.user_data.clear()
    msg = await update.message.reply_text("كم قيمة المشتريات؟ اكتب الرقم مثلا (15)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancelpay")]]))
    context.user_data['last_msg_id'] = msg.message_id
    return PAY_AMOUNT

async def get_pay_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amt = update.message.text.strip()
    if not amt.isdigit(): return PAY_AMOUNT
    context.user_data['pay_amount'] = amt
    text = (f"ارفع الإشعار بالمبلغ ({amt}) شيكل.\n\n🔹 محفظة بال باي:\n`{WALLET_NUMBER}`\n🔹 بنك فلسطين:\n`1512081`\n`PS11PALS045115120810993100000`")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✏️ تعديل", callback_data="editpay")],[InlineKeyboardButton("❌ إلغاء", callback_data="cancelpay")]]))
    return PAY_RECEIPT

async def get_pay_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return PAY_RECEIPT
    user, amt = update.message.from_user, int(context.user_data.get('pay_amount', 0))
    with get_db() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO orders (user_id, details, total_price, location, timestamp, status, is_paid) VALUES (%s, %s, %s, %s, %s, %s, %s)", (user.id, "شراء مباشر", amt, "دفع فوري", get_pal_time(), "مقبول", 1))
        conn.commit()
    await update.message.reply_text("شكراً لك! تم التسجيل بنجاح. 🌸")
    await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id, caption=f"💰 شراء مباشر: {amt} ش من {user.first_name}")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_pay_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); await query.edit_message_text("✅ تم الإلغاء."); context.user_data.clear()
    return ConversationHandler.END

async def settle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    context.user_data['settle_amount'] = query.data.split("_")[1]
    text = (f"ارفع إيصال تحويل ({context.user_data['settle_amount']}) شيكل 👇\n\n🔹 بال باي: `{WALLET_NUMBER}`\n🔹 بنك فلسطين: `1512081`")
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="cancelpay")]]))
    return SETTLING_DEBT

async def receive_debt_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo: return SETTLING_DEBT
    user = update.message.from_user
    await update.message.reply_text("وصل للكاشير للتأكيد. ❤️")
    await context.bot.send_photo(chat_id=CASHIER_ID, photo=update.message.photo[-1].file_id, caption=f"💰 طلب تسديد دين: {context.user_data.get('settle_amount')} ش من {user.first_name}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأكيد وتصفير", callback_data=f"clear_{user.id}")]]))
    context.user_data.clear()
    return ConversationHandler.END

# --- دوال الأدمن والتقييم (بدون تغيير) ---

async def cashier_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.from_user.id != CASHIER_ID: return
    data = query.data.split("_")
    with get_db() as conn:
        c = conn.cursor()
        if data[0] == "conf":
            user_id, order_id = int(data[1]), int(data[2])
            c.execute("UPDATE orders SET status='مقبول' WHERE id=%s", (order_id,))
            conn.commit()
            await query.edit_message_text(query.message.text + "\n\n✅ تم التأكيد.")
            await context.bot.send_message(chat_id=user_id, text=f"✅ **تم تأكيد طلبك!**\nصحة وهنا! ❤️", parse_mode='Markdown')
        elif data[0] == "out":
            user_id, order_id = data[1], data[2]
            c.execute("SELECT details FROM orders WHERE id=%s", (order_id,))
            items = [it.strip() for it in c.fetchone()[0].split(",")]
            keyboard = [[InlineKeyboardButton(f"❌ {it} غير متوفر", callback_data=f"rmv_{user_id}_{order_id}_{i}")] for i, it in enumerate(items)]
            await query.edit_message_text(f"اختار الصنف الناقص:", reply_markup=InlineKeyboardMarkup(keyboard))

async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("الرجاء الضغط على زر **البدء (Start)** أو استخدام **القائمة ☰**.", parse_mode='Markdown')

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    main_conv = ConversationHandler(
        entry_points=[CommandHandler('start', start), CommandHandler('pay', start_instant_pay), CallbackQueryHandler(customer_handle_edit, pattern="^edit(back|ready)_"), CallbackQueryHandler(settle_start, pattern="^settle_"), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_order)],
        states={
            ASK_OFFICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_office_and_show_menu)],
            CHOOSING_CATEGORY: [CallbackQueryHandler(category_choice, pattern="^cat_")],
            CHOOSING_SERVICE: [CallbackQueryHandler(service_choice, pattern="^(item_|back_to_main$)")],
            CONFIRMING_CART: [CallbackQueryHandler(confirm_cart, pattern=r"^(remove_\d+|add_more|confirm_order|cancel_order)$")],
            LOCATION_TYPE: [CallbackQueryHandler(location_choice, pattern="^loc_")],
            PAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pay_amount), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")],
            PAY_RECEIPT: [MessageHandler(filters.PHOTO | filters.Document.ALL, get_pay_receipt), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$"), CallbackQueryHandler(edit_pay_amount, pattern="^editpay$")],
            SETTLING_DEBT: [MessageHandler(filters.PHOTO | filters.Document.ALL, receive_debt_receipt), CallbackQueryHandler(cancel_pay_flow, pattern="^cancelpay$")]
        },
        fallbacks=[CommandHandler('cancel', cancel_command)]
    )
    app.add_handler(main_conv)
    app.add_handler(CallbackQueryHandler(cashier_action, pattern="^(conf|out)_"))
    app.add_handler(CommandHandler('ledger_admin', admin_ledger))
    app.add_handler(CommandHandler('ledger', lambda u, c: user_ledger(u, c)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))
    app.run_webhook(listen="0.0.0.0", port=PORT, secret_token="SecretPassword123", webhook_url=WEBHOOK_DOMAIN, drop_pending_updates=True)

if __name__ == '__main__': main()
