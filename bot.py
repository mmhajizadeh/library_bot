import sqlite3
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler, # اضافه شده برای مکالمه
)

# توکن ربات خود را اینجا قرار دهید
TOKEN = "8548212605:AAHqcczpKhO9YUcJyiQbJcZ3LnqcymMRYf8"


# فعال کردن لاگینگ برای خطایابی
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- تعریف حالت‌های مکالمه برای افزودن کتاب ---
GET_TITLE, GET_AUTHOR, GET_SUBJECT, GET_COUNT = range(4)


# --- توابع کمکی دیتابیس ---

def db_query(query, params=()):
    """یک تابع کمکی برای اتصال و اجرای کوئری در دیتابیس"""
    conn = None
    try:
        conn = sqlite3.connect('library.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if query.strip().upper().startswith("SELECT"):
            results = cursor.fetchall()
            return results
        else:
            conn.commit()
            return cursor.lastrowid
            
    except sqlite3.Error as e:
        logger.error(f"خطای دیتابیس: {e} | کوئری: {query} | پارامترها: {params}")
        return None
    finally:
        if conn:
            conn.close()

def init_db():
    """ایجاد جداول مورد نیاز در صورت عدم وجود"""
    logger.info("در حال بررسی و ایجاد جداول دیتابیس...")
    # جدول books برای نگهداری مشخصات کتاب‌ها
    db_query("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT,
            subject TEXT,
            count INTEGER NOT NULL
        )
    """)
    # جدول admins برای نگهداری user_id مدیران
    db_query("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)
    # اگر هیچ ادمینی وجود ندارد، اولین کاربری که ربات را اجرا می‌کند ادمین خواهد بود
    if not db_query("SELECT 1 FROM admins LIMIT 1"):
        logger.warning("جدول ادمین خالی است. اولین کاربری که /start را بزند، ادمین خواهد شد.")

def is_admin(user_id):
    """چک می‌کند آیا کاربر ادمین است یا خیر"""
    query = "SELECT 1 FROM admins WHERE user_id = ?"
    result = db_query(query, (user_id,))
    return bool(result)

# --- Handlers عمومی ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /start را مدیریت می‌کند"""
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    # اگر ادمین اولیه تعریف نشده باشد، کاربر فعلی را ادمین می‌کند
    if not db_query("SELECT 1 FROM admins LIMIT 1"):
        db_query("INSERT INTO admins (user_id) VALUES (?)", (user_id,))
        logger.warning(f"کاربر {user_id} ({first_name}) به عنوان اولین ادمین ثبت شد.")
        
    welcome_text = f"سلام {first_name}، به ربات کتابخانه خوابگاه خوش آمدید!\n"
    
    if is_admin(user_id):
        welcome_text += "شما به پنل ادمین دسترسی دارید."
        keyboard = [
            ['📚 افزودن کتاب', '🔍 جستجوی کتاب'],
            ['📦 لیست درخواست‌ها', '📊 آمار']
        ]
    else:
        welcome_text += "می‌توانید از دکمه‌های زیر استفاده کنید:"
        keyboard = [
            ['🔍 جستجوی کتاب', '🏷 فیلتر موضوعی'],
            ['📕 کتاب‌های من']
        ]
        
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)


# --- Handlers مربوط به افزودن کتاب (ConversationHandler) ---

async def add_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند افزودن کتاب (فقط برای ادمین‌ها)"""
    user_id = update.effective_user.id
    
    # فیلتر ادمین: اگر ادمین نیست، اجازه شروع نده
    if not is_admin(user_id):
        await update.message.reply_text("شما اجازه دسترسی به این بخش را ندارید.")
        return ConversationHandler.END

    # تنظیمات موقت: ContextTypes.user_data برای ذخیره موقت اطلاعات
    context.user_data['book_data'] = {}
    
    # دکمه لغو را برای کاربر نمایش می‌دهیم
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "📚 لطفا **نام کامل کتاب** را وارد کنید:",
        reply_markup=reply_markup
    )
    
    # به حالت بعدی (دریافت عنوان) می‌رویم
    return GET_TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت نام کتاب و درخواست نویسنده"""
    title = update.message.text
    context.user_data['book_data']['title'] = title
    
    await update.message.reply_text(f"نام کتاب: **{title}** ثبت شد.\n\n✍️ حالا لطفا **نام نویسنده** را وارد کنید:")
    
    # به حالت بعدی (دریافت نویسنده) می‌رویم
    return GET_AUTHOR

async def get_author(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت نام نویسنده و درخواست موضوع"""
    author = update.message.text
    context.user_data['book_data']['author'] = author
    
    # می‌توانیم موضوعات رایج را به عنوان دکمه پیشنهاد دهیم
    subject_keyboard = [
        ['داستان', 'علمی-تخیلی', 'روانشناسی'],
        ['تاریخی', 'درسی', 'سایر'],
        ['لغو عملیات']
    ]
    reply_markup = ReplyKeyboardMarkup(subject_keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        f"نویسنده: **{author}** ثبت شد.\n\n🏷 لطفا **موضوع کتاب** را انتخاب کنید یا وارد نمایید:",
        reply_markup=reply_markup
    )
    
    # به حالت بعدی (دریافت موضوع) می‌رویم
    return GET_SUBJECT

async def get_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت موضوع کتاب و درخواست تعداد موجودی"""
    subject = update.message.text
    context.user_data['book_data']['subject'] = subject
    
    await update.message.reply_text(f"موضوع: **{subject}** ثبت شد.\n\n🔢 در نهایت، لطفا **تعداد موجودی** این کتاب در انبار را وارد کنید (باید یک عدد باشد):",
                                    reply_markup=ReplyKeyboardRemove())
    
    # به حالت بعدی (دریافت موجودی) می‌رویم
    return GET_COUNT

async def get_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت موجودی، ذخیره در دیتابیس و پایان مکالمه"""
    count_text = update.message.text
    
    # اعتبارسنجی: بررسی کنیم آیا ورودی عدد است
    try:
        count = int(count_text)
        if count < 1:
             raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: موجودی باید یک عدد صحیح و بزرگتر از صفر باشد. لطفا دوباره وارد کنید:",
                                        reply_markup=ReplyKeyboardRemove())
        return GET_COUNT # در همین حالت باقی می‌مانیم تا ورودی صحیح داده شود

    book_data = context.user_data['book_data']
    
    # ذخیره در دیتابیس
    query = "INSERT INTO books (title, author, subject, count) VALUES (?, ?, ?, ?)"
    params = (book_data['title'], book_data['author'], book_data['subject'], count)
    last_id = db_query(query, params)
    
    if last_id is not None:
        await update.message.reply_text(
            f"✅ کتاب **{book_data['title']}** (ID: {last_id}) با موفقیت به کتابخانه اضافه شد.\n"
            f"نویسنده: {book_data['author']}، موضوع: {book_data['subject']}، موجودی: {count}",
            reply_markup=ReplyKeyboardMarkup([
                ['📚 افزودن کتاب', '🔍 جستجوی کتاب'],
                ['📦 لیست درخواست‌ها', '📊 آمار']
            ], resize_keyboard=True)
        )
    else:
        await update.message.reply_text(
            "❌ متاسفانه خطایی در ذخیره اطلاعات در دیتابیس رخ داد.",
            reply_markup=ReplyKeyboardRemove()
        )
    
    # پاک کردن داده‌های موقت و پایان مکالمه
    context.user_data.pop('book_data', None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """لغو مکالمه و بازگشت به منوی اصلی"""
    context.user_data.pop('book_data', None)
    
    # کیبورد ادمین را دوباره نمایش می‌دهیم
    keyboard = [
        ['📚 افزودن کتاب', '🔍 جستجوی کتاب'],
        ['📦 لیست درخواست‌ها', '📊 آمار']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text("❌ عملیات افزودن کتاب لغو شد. به منوی اصلی بازگشتید.", 
                                    reply_markup=reply_markup)
    
    return ConversationHandler.END


def main() -> None:
    """تابع اصلی راه‌اندازی ربات"""
    
    # 1. مطمئن شدن از وجود جداول
    init_db() 
    
    if TOKEN == "YOUR_TOKEN_HERE":
        logger.error("!!! توکن ربات تنظیم نشده است!")
        return

    # # ساخت کلاینت httpx با تنظیمات پراکسی (در صورت وجود)
    # httpx_options = {}
    # if PROXY_URL:
    #     # اگر پراکسی تعیین شده، آن را به عنوان آرگومان به httpx می‌دهیم
    #     httpx_options["proxy"] = PROXY_URL
    #     logger.info(f"استفاده از پراکسی: {PROXY_URL}")
        
    logger.info("در حال ساخت Application...")
    
    # ساخت Application و تزریق تنظیمات httpx
    application = Application.builder()\
        .token(TOKEN)\
        .concurrent_updates(True)\
        .build()
        # .httpx_request_kwargs(httpx_options)\


    # --- تنظیمات Handlers ---
    
    # 1. Handler دستور /start
    application.add_handler(CommandHandler("start", start))
    
    # 2. Handler افزودن کتاب (ConversationHandler)
    add_book_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^📚 افزودن کتاب$'), add_book_start)
        ],
        states={
            GET_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_title)],
            GET_AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_author)],
            GET_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_subject)],
            GET_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_count)],
        },
        fallbacks=[
            MessageHandler(filters.Regex('^لغو عملیات$') | filters.COMMAND, cancel)
        ]
    )
    application.add_handler(add_book_handler)
    
    # 3. Handler برای پیام‌های ناشناخته
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start)) # فعلاً کاربر را به منوی اصلی هدایت می‌کند
    
    logger.info("ربات در حال راه‌اندازی است (Polling)...")
    
    # اجرای ربات
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()