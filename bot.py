import os
import psycopg2
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# --- توکن و تنظیمات ---
# توکن شما از @BotFather
TOKEN = "8548212605:AAHqcczpKhO9YUcJyiQbJcZ3LnqcymMRYf8"

# آدرس دیتابیس PostgreSQL که توسط Railway به عنوان یک متغیر محیطی تزریق می شود
DATABASE_URL = os.environ.get('DATABASE_URL') 
if not DATABASE_URL:
    logging.error("DATABASE_URL پیدا نشد. لطفا سرویس PostgreSQL را به پروژه متصل کنید.")
# -------------------------

# --- فعال کردن لاگینگ ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- تعریف حالت‌های مکالمه ---
# برای افزودن کتاب
GET_TITLE, GET_AUTHOR, GET_SUBJECT, GET_COUNT = range(4)
# برای جستجوی کتاب
SEARCH_QUERY = 4
# برای ویرایش موجودی (جدید)
EDIT_GET_ID, EDIT_GET_NEW_COUNT = range(5, 7)


# --- توابع کمکی دیتابیس (تغییر یافته برای PostgreSQL) ---

def db_query(query, params=()):
    """یک تابع کمکی برای اتصال و اجرای کوئری در دیتابیس PostgreSQL"""
    conn = None
    try:
        # اتصال به PostgreSQL با استفاده از متغیر محیطی
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # در PostgreSQL از %s به جای ? استفاده می شود
        cursor.execute(query, params)
        
        if query.strip().upper().startswith("SELECT"):
            results = cursor.fetchall()
            return results
        else:
            conn.commit()
            # در PostgreSQL، برای گرفتن آخرین ID از متد جداگانه استفاده می کنیم
            if query.strip().upper().startswith("INSERT"):
                # فرض می کنیم INSERT همیشه یک ID برمی گرداند
                return "COMMIT_OK" 
            return "COMMIT_OK"
            
    except psycopg2.Error as e:
        logger.error(f"خطای دیتابیس (PostgreSQL): {e} | کوئری: {query} | پارامترها: {params}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()

def init_db():
    """ایجاد جداول مورد نیاز برای PostgreSQL"""
    logger.info("در حال بررسی و ایجاد جداول دیتابیس PostgreSQL...")
    
    # کوئری ها باید برای سینتکس PostgreSQL بهینه شوند
    
    # ۱. ایجاد جدول books (استفاده از SERIAL PRIMARY KEY به جای INTEGER PRIMARY KEY)
    db_query("""
        CREATE TABLE IF NOT EXISTS books (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            subject TEXT,
            count INTEGER NOT NULL, 
            borrowed_count INTEGER DEFAULT 0 
        )
    """)

    # ۲. ایجاد جدول admins (user_id باید BIGINT باشد تا ID تلگرام را نگه دارد)
    db_query("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY
        )
    """)
    
    # ۳. بررسی ادمین اولیه
    if not db_query("SELECT 1 FROM admins LIMIT 1"):
        logger.warning("جدول ادمین خالی است.")
        
def is_admin(user_id):
    """چک می‌کند آیا کاربر ادمین است یا خیر"""
    # در PostgreSQL از %s برای placeholder استفاده می کنیم
    query = "SELECT 1 FROM admins WHERE user_id = %s"
    result = db_query(query, (user_id,))
    return bool(result)

# --- Handlers عمومی و ناوبری ---

def get_keyboard(user_id):
    """ساخت کیبورد بر اساس نقش کاربر (دکمه ویرایش موجودی اضافه شد)"""
    if is_admin(user_id):
        return ReplyKeyboardMarkup([
            ['📚 افزودن کتاب', '🔍 جستجوی کتاب'],
            ['✏️ ویرایش موجودی', '📦 لیست درخواست‌ها'], 
            # ['📊 آمار']
        ], resize_keyboard=True, one_time_keyboard=False)
    else:
        return ReplyKeyboardMarkup([
            ['🔍 جستجوی کتاب', '🏷 فیلتر موضوعی'],
            ['📕 کتاب‌های من']
        ], resize_keyboard=True, one_time_keyboard=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /start را مدیریت می‌کند"""
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    # ادمین اولیه
    if not db_query("SELECT 1 FROM admins LIMIT 1"):
        # در PostgreSQL، استفاده از %s ضروری است
        db_query("INSERT INTO admins (user_id) VALUES (%s)", (user_id,))
        logger.warning(f"کاربر {user_id} ({first_name}) به عنوان اولین ادمین ثبت شد.")
        
    welcome_text = f"سلام {first_name}، به ربات کتابخانه خوابگاه خوش آمدید!\n"
    if is_admin(user_id):
        welcome_text += "شما به پنل ادمین دسترسی دارید."
    
    await update.message.reply_text(welcome_text, reply_markup=get_keyboard(user_id))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """لغو مکالمه و بازگشت به منوی اصلی"""
    context.user_data.clear()
    
    await update.message.reply_text(
        "❌ عملیات لغو شد. به منوی اصلی بازگشتید.", 
        reply_markup=get_keyboard(update.effective_user.id)
    )
    
    return ConversationHandler.END


# --- (بخش ۱) Handlers مربوط به افزودن کتاب ---
async def add_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند افزودن کتاب (فقط برای ادمین‌ها)"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما اجازه دسترسی به این بخش را ندارید.", reply_markup=get_keyboard(user_id))
        return ConversationHandler.END
    context.user_data['book_data'] = {}
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("📚 لطفا **نام کامل کتاب** را وارد کنید:", reply_markup=reply_markup)
    return GET_TITLE
async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت نام کتاب و درخواست نویسنده"""
    context.user_data['book_data']['title'] = update.message.text
    await update.message.reply_text("✍️ حالا لطفا **نام نویسنده** را وارد کنید:")
    return GET_AUTHOR
async def get_author(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت نام نویسنده و درخواست موضوع"""
    context.user_data['book_data']['author'] = update.message.text
    subject_keyboard = [['داستان', 'علمی-تخیلی', 'روانشناسی'], ['تاریخی', 'درسی', 'سایر'], ['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(subject_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("🏷 لطفا **موضوع کتاب** را انتخاب کنید یا وارد نمایید:", reply_markup=reply_markup)
    return GET_SUBJECT
async def get_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت موضوع کتاب و درخواست تعداد موجودی"""
    context.user_data['book_data']['subject'] = update.message.text
    await update.message.reply_text("🔢 در نهایت، لطفا **تعداد کل موجودی** این کتاب را وارد کنید (عدد):", reply_markup=ReplyKeyboardRemove())
    return GET_COUNT
async def get_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت موجودی، ذخیره در دیتابیس و پایان مکالمه"""
    try:
        count = int(update.message.text)
        if count < 1: raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: موجودی باید یک عدد صحیح و بزرگتر از صفر باشد. لطفا دوباره وارد کنید:", reply_markup=ReplyKeyboardRemove())
        return GET_COUNT 
    book_data = context.user_data['book_data']
    
    # کوئری PostgreSQL با %s و RETURNING id برای گرفتن ID کتاب
    query = "INSERT INTO books (title, author, subject, count) VALUES (%s, %s, %s, %s) RETURNING id"
    params = (book_data['title'], book_data['author'], book_data['subject'], count)
    
    # اجرای کوئری و گرفتن ID
    conn = None
    last_id = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute(query, params)
        last_id = cursor.fetchone()[0]
        conn.commit()
    except psycopg2.Error as e:
        logger.error(f"خطای INSERT در PostgreSQL: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

    if last_id is not None:
        await update.message.reply_text(f"✅ کتاب **{book_data['title']}** (ID: {last_id}) با موفقیت اضافه شد.", reply_markup=get_keyboard(update.effective_user.id))
    else:
        await update.message.reply_text("❌ خطایی در ذخیره اطلاعات رخ داد.", reply_markup=get_keyboard(update.effective_user.id))
        
    context.user_data.clear()
    return ConversationHandler.END


# --- (بخش ۲) Handlers مربوط به جستجوی کتاب ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند جستجوی کتاب"""
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("🔍 لطفا **نام کتاب، نویسنده یا موضوع** مورد نظر خود را برای جستجو وارد کنید:", reply_markup=reply_markup)
    return SEARCH_QUERY

async def execute_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت کوئری جستجو و اجرای کوئری در دیتابیس"""
    query_text = update.message.text
    search_term = f'%{query_text}%'
    
    # PostgreSQL: از ILIKE برای جستجوی Case-Insensitive استفاده می کنیم
    sql_query = """
        SELECT id, title, author, subject, count, borrowed_count FROM books 
        WHERE title ILIKE %s OR author ILIKE %s OR subject ILIKE %s
        LIMIT 10
    """
    
    # توجه: درایور psycopg2 یک Tuple از پارامترها را انتظار دارد
    results = db_query(sql_query, (search_term, search_term, search_term))
    
    if results:
        response_text = f"✅ {len(results)} کتاب با عبارت **'{query_text}'** پیدا شد:\n\n"
        
        for book_id, title, author, subject, count, borrowed in results:
            available = count - (borrowed or 0) # موجودی موجود = کل - قرض گرفته شده
            response_text += (
                f"**📕 {title}**\n"
                f"    🆔: {book_id}\n"
                f"    ✍️: {author}\n"
                f"    🏷: {subject}\n"
                f"    ⬅️ موجودی: {available} (از کل {count} عدد)\n"
                f"---------------------------------\n"
            )
    else:
        response_text = f"❌ متأسفانه کتابی با عبارت **'{query_text}'** پیدا نشد."

    await update.message.reply_text(
        response_text,
        reply_markup=get_keyboard(update.effective_user.id)
    )
    
    return ConversationHandler.END


# --- (بخش ۳) Handlers مربوط به ویرایش موجودی (جدید) ---

async def edit_count_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند ویرایش موجودی (فقط ادمین)"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما اجازه دسترسی به این بخش را ندارید.", reply_markup=get_keyboard(user_id))
        return ConversationHandler.END

    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "✏️ لطفا **ID کتاب**ی که می‌خواهید موجودی آن را ویرایش کنید، وارد نمایید.\n"
        "(ID را می‌توانید از '🔍 جستجوی کتاب' پیدا کنید)",
        reply_markup=reply_markup
    )
    return EDIT_GET_ID

async def get_book_id_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت ID و درخواست موجودی جدید"""
    try:
        book_id = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: ID باید یک عدد باشد. لطفا دوباره ID کتاب را وارد کنید:")
        return EDIT_GET_ID

    # کتاب را پیدا کن
    book = db_query("SELECT title, count, borrowed_count FROM books WHERE id = %s", (book_id,))
    if not book:
        await update.message.reply_text(f"⚠️ خطا: کتابی با ID {book_id} پیدا نشد. لطفا دوباره ID را وارد کنید:")
        return EDIT_GET_ID

    # ذخیره ID برای مرحله بعد
    context.user_data['edit_book_id'] = book_id
    title, current_count, borrowed_count = book[0]

    await update.message.reply_text(
        f"کتاب: **{title}** (ID: {book_id})\n"
        f"موجودی فعلی (کل): {current_count}\n"
        f"تعداد قرض گرفته شده: {borrowed_count}\n\n"
        f"🔢 لطفا **موجودی کل جدید** را وارد کنید.\n"
        f"(توجه: این عدد باید بزرگتر یا مساوی با تعداد قرض گرفته شده ({borrowed_count}) باشد):",
        reply_markup=ReplyKeyboardRemove()
    )
    return EDIT_GET_NEW_COUNT

async def get_new_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت موجودی جدید و آپدیت دیتابیس"""
    try:
        new_count = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: موجودی باید یک عدد صحیح باشد. لطفا دوباره وارد کنید:")
        return EDIT_GET_NEW_COUNT

    book_id = context.user_data['edit_book_id']
    
    # اعتبارسنجی: موجودی کل جدید نباید کمتر از تعداد قرض گرفته شده باشد
    book_info = db_query("SELECT title, borrowed_count FROM books WHERE id = %s", (book_id,))
    title, borrowed_count = book_info[0] if book_info else ("N/A", 0)
    
    if new_count < borrowed_count:
        await update.message.reply_text(
            f"❌ خطا: موجودی کل جدید ({new_count}) نمی‌تواند کمتر از تعداد کتاب‌های قرض گرفته شده ({borrowed_count}) باشد.\n"
            f"لطفا یک عدد بزرگتر یا مساوی {borrowed_count} وارد کنید:",
            reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_GET_NEW_COUNT

    # آپدیت دیتابیس (با %s)
    db_query("UPDATE books SET count = %s WHERE id = %s", (new_count, book_id))
    
    await update.message.reply_text(
        f"✅ موجودی کل کتاب **{title}** (ID: {book_id}) با موفقیت به **{new_count}** عدد تغییر یافت.",
        reply_markup=get_keyboard(update.effective_user.id)
    )
    
    context.user_data.clear()
    return ConversationHandler.END


# --- تابع اصلی ---

def main() -> None:
    """تابع اصلی راه‌اندازی ربات"""
    
    # ۱. اطمینان از وجود جداول دیتابیس
    init_db() 
    
    # ۲. ساخت Application
    logger.info("در حال ساخت Application...")
    
    application_builder = Application.builder().token(TOKEN).concurrent_updates(True)
    application = application_builder.build()

    # --- تنظیمات Handlers ---
    
    # ۱. مکالمه افزودن کتاب
    add_book_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📚 افزودن کتاب$'), add_book_start)],
        states={
            GET_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_title)],
            GET_AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_author)],
            GET_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_subject)],
            GET_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_count)],
        },
        fallbacks=[MessageHandler(filters.Regex('^لغو عملیات$') | filters.COMMAND, cancel)]
    )

    # ۲. مکالمه جستجوی کتاب
    search_book_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔍 جستجوی کتاب$'), search_start)],
        states={
            SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), execute_search)],
        },
        fallbacks=[MessageHandler(filters.Regex('^لغو عملیات$') | filters.COMMAND, cancel)]
    )

    # ۳. مکالمه ویرایش موجودی
    edit_count_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^✏️ ویرایش موجودی$'), edit_count_start)],
        states={
            EDIT_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_book_id_for_edit)],
            EDIT_GET_NEW_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_new_count)],
        },
        fallbacks=[MessageHandler(filters.Regex('^لغو عملیات$') | filters.COMMAND, cancel)]
    )

    # افزودن تمام Handler ها به ربات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(add_book_handler)
    application.add_handler(search_book_handler)    
    application.add_handler(edit_count_handler)     
    
    # Handler برای پیام‌های ناشناخته 
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))
    
    # --- راه‌اندازی ربات ---
    logger.info("ربات در حال راه‌اندازی است (Polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
