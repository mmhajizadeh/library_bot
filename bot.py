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
# This token should be read from environment variables in a real application, 
# but for this example, we keep it here.
TOKEN = "8548212605:AAHqcczpKhO9YUcJyiQbC7LnqcymMRYf8"
DATABASE_URL = os.environ.get('DATABASE_URL') 
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
# برای ویرایش موجودی
EDIT_GET_ID, EDIT_GET_NEW_COUNT = range(5, 7)
# برای امانت کتاب
BORROW_GET_ID = 7
# برای بازگرداندن کتاب
RETURN_GET_LOAN_ID = 8 
# برای نمایش جزئیات کتاب
DETAILS_GET_ID = 9
# برای حذف کتاب
DELETE_GET_ID, DELETE_CONFIRM = range(10, 12)


# --- توابع کمکی دیتابیس ---

def db_query(query, params=()):
    """یک تابع کمکی برای اتصال و اجرای کوئری در دیتابیس PostgreSQL"""
    if not DATABASE_URL:
        # اگر DATABASE_URL تعریف نشده باشد، از اجرا جلوگیری می کند
        logger.error("خطا: DATABASE_URL در دسترس نیست. نمی‌توان به دیتابیس متصل شد.")
        return None
        
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        cursor.execute(query, params)
        
        if query.strip().upper().startswith("SELECT"):
            results = cursor.fetchall()
            return results
        else:
            conn.commit()
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
    if not DATABASE_URL:
        logger.error("خطا: DATABASE_URL در دسترس نیست. جداول ایجاد نشدند.")
        return
        
    logger.info("در حال بررسی و ایجاد جداول دیتابیس PostgreSQL...")
    
    # اطمینان از وجود تمام جداول
    
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

    db_query("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY
        )
    """)
    
    db_query("""
        CREATE TABLE IF NOT EXISTS loans (
            id SERIAL PRIMARY KEY,
            book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            borrow_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            return_date TIMESTAMP DEFAULT NULL
        )
    """)
        
def is_admin(user_id):
    """چک می‌کند آیا کاربر ادمین است یا خیر"""
    query = "SELECT 1 FROM admins WHERE user_id = %s"
    result = db_query(query, (user_id,))
    return bool(result)

# --- Handlers عمومی و ناوبری ---

def get_keyboard(user_id):
    """ساخت کیبورد بر اساس نقش کاربر"""
    if is_admin(user_id):
        return ReplyKeyboardMarkup([
            ['📚 افزودن کتاب', '🔍 جستجوی کتاب'],
            ['✏️ ویرایش موجودی', '🗑️ حذف کتاب'], # دکمه حذف جدید
            ['🔎 جزئیات کتاب', '📦 لیست امانت‌ها'], # دکمه جزئیات جدید
        ], resize_keyboard=True, one_time_keyboard=False)
    else:
        return ReplyKeyboardMarkup([
            ['🔍 جستجوی کتاب', '🤝 امانت کتاب'], 
            ['📕 کتاب‌های من', '↩️ بازگشت کتاب'],
            ['🔎 جزئیات کتاب'] # دکمه جزئیات برای کاربران عادی
        ], resize_keyboard=True, one_time_keyboard=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /start را مدیریت می‌کند"""
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    if not DATABASE_URL:
        await update.message.reply_text(f"سلام {first_name}، ربات موقتاً در حالت تعمیر و نگهداری دیتابیس قرار دارد. لطفاً بعداً امتحان کنید.")
        return

    welcome_text = f"سلام {first_name}، به ربات کتابخانه خوش آمدید!\n"
    
    # ادمین کردن اولین کاربر
    if not is_admin(user_id) and not db_query("SELECT 1 FROM admins LIMIT 1"):
        db_query("INSERT INTO admins (user_id) VALUES (%s)", (user_id,))
        welcome_text += "شما به عنوان **اولین ادمین** ثبت شدید. به پنل ادمین دسترسی دارید."
    elif is_admin(user_id):
        welcome_text += "شما به پنل ادمین دسترسی دارید."
    else:
        welcome_text += "شما به عنوان کاربر عادی وارد شدید."

    await update.message.reply_text(welcome_text, reply_markup=get_keyboard(user_id))

async def add_admin_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /addadmin را مدیریت می‌کند و ID عددی کاربر را برمی‌گرداند."""
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"✅ شناسه عددی (User ID) شما: `{user_id}`\n\n"
        "اگر می‌خواهید ادمین شوید، باید این ID را به صورت دستی در جدول `admins` دیتابیس PostgreSQL وارد کنید.",
        parse_mode='Markdown'
    )
    
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
    
    query = "INSERT INTO books (title, author, subject, count) VALUES (%s, %s, %s, %s) RETURNING id"
    params = (book_data['title'], book_data['author'], book_data['subject'], count)
    
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
    
    sql_query = """
        SELECT id, title, author, subject, count, borrowed_count FROM books 
        WHERE title ILIKE %s OR author ILIKE %s OR subject ILIKE %s
        LIMIT 10
    """
    
    results = db_query(sql_query, (search_term, search_term, search_term))
    
    if results:
        response_text = f"✅ {len(results)} کتاب با عبارت **'{query_text}'** پیدا شد:\n\n"
        
        for book_id, title, author, subject, count, borrowed in results:
            # Check for None in borrowed_count (though it should be 0 by default)
            borrowed = borrowed or 0
            available = count - borrowed 
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
        reply_markup=get_keyboard(update.effective_user.id),
        parse_mode='Markdown'
    )
    
    return ConversationHandler.END


# --- (بخش ۳) Handlers مربوط به ویرایش موجودی ---

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

    book = db_query("SELECT title, count, borrowed_count FROM books WHERE id = %s", (book_id,))
    if not book:
        await update.message.reply_text(f"⚠️ خطا: کتابی با ID {book_id} پیدا نشد. لطفا دوباره ID را وارد کنید:")
        return EDIT_GET_ID

    context.user_data['edit_book_id'] = book_id
    title, current_count, borrowed_count = book[0]

    await update.message.reply_text(
        f"کتاب: **{title}** (ID: {book_id})\n"
        f"موجودی فعلی (کل): {current_count}\n"
        f"تعداد قرض گرفته شده: {borrowed_count}\n\n"
        f"🔢 لطفا **موجودی کل جدید** را وارد کنید.\n"
        f"(توجه: این عدد باید بزرگتر یا مساوی با تعداد قرض گرفته شده ({borrowed_count}) باشد):",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
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
    
    book_info = db_query("SELECT title, borrowed_count FROM books WHERE id = %s", (book_id,))
    title, borrowed_count = book_info[0] if book_info else ("N/A", 0)
    
    if new_count < borrowed_count:
        await update.message.reply_text(
            f"❌ خطا: موجودی کل جدید ({new_count}) نمی‌تواند کمتر از تعداد کتاب‌های قرض گرفته شده ({borrowed_count}) باشد.\n"
            f"لطفا یک عدد بزرگتر یا مساوی {borrowed_count} وارد کنید:",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )
        return EDIT_GET_NEW_COUNT

    db_query("UPDATE books SET count = %s WHERE id = %s", (new_count, book_id))
    
    await update.message.reply_text(
        f"✅ موجودی کل کتاب **{title}** (ID: {book_id}) با موفقیت به **{new_count}** عدد تغییر یافت.",
        reply_markup=get_keyboard(update.effective_user.id),
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return ConversationHandler.END


# --- (بخش ۴) Handlers مربوط به امانت کتاب ---

async def borrow_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند امانت کتاب"""
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "🤝 لطفا **ID کتابی** که می‌خواهید امانت بگیرید را وارد کنید.\n"
        "(ID را از قسمت '🔍 جستجوی کتاب' پیدا کنید.)",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return BORROW_GET_ID

async def process_borrow_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بررسی ID و ثبت امانت کتاب"""
    user_id = update.effective_user.id
    
    try:
        book_id = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: ID کتاب باید یک عدد باشد. لطفا دوباره وارد کنید:")
        return BORROW_GET_ID
    
    # 1. بازیابی اطلاعات کتاب و بررسی موجودی
    book_info = db_query("SELECT title, count, borrowed_count FROM books WHERE id = %s", (book_id,))
    
    if not book_info:
        await update.message.reply_text(f"⚠️ خطا: کتابی با ID {book_id} پیدا نشد. لطفا ID صحیح را وارد کنید:")
        return BORROW_GET_ID
        
    title, total_count, borrowed_count = book_info[0]
    borrowed_count = borrowed_count or 0 # Ensure it's not None
    available_count = total_count - borrowed_count
    
    if available_count <= 0:
        await update.message.reply_text(f"❌ متأسفانه کتاب **{title}** (ID: {book_id}) در حال حاضر موجود نیست.", reply_markup=get_keyboard(user_id), parse_mode='Markdown')
        return ConversationHandler.END

    # 2. بررسی اینکه آیا کاربر قبلاً این کتاب را امانت نگرفته است
    loan_check_query = """
        SELECT id FROM loans 
        WHERE user_id = %s AND book_id = %s AND return_date IS NULL
    """
    existing_loan = db_query(loan_check_query, (user_id, book_id))
    
    if existing_loan:
        await update.message.reply_text(
            f"❌ شما قبلاً کتاب **{title}** را امانت گرفته‌اید و آن را برنگردانده‌اید.", 
            reply_markup=get_keyboard(user_id),
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    # 3. ثبت امانت و به روز رسانی موجودی در یک تراکنش
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # A. ثبت ردیف جدید در جدول loans
        insert_loan_query = "INSERT INTO loans (book_id, user_id) VALUES (%s, %s)"
        cursor.execute(insert_loan_query, (book_id, user_id))
        
        # B. افزایش borrowed_count در جدول books
        update_book_query = "UPDATE books SET borrowed_count = borrowed_count + 1 WHERE id = %s"
        cursor.execute(update_book_query, (book_id,))
        
        conn.commit()
        
        await update.message.reply_text(
            f"✅ کتاب **{title}** (ID: {book_id}) با موفقیت برای شما امانت گرفته شد.\n"
            f"موجودی در دسترس باقیمانده: **{available_count - 1}**",
            reply_markup=get_keyboard(user_id),
            parse_mode='Markdown'
        )
        
    except psycopg2.Error as e:
        logger.error(f"خطا در ثبت امانت (Transaction Failed): {e}")
        if conn: conn.rollback()
        await update.message.reply_text("❌ خطایی در ثبت امانت رخ داد. لطفا دوباره تلاش کنید.", reply_markup=get_keyboard(user_id))
        
    finally:
        if conn: conn.close()
        context.user_data.clear()
        return ConversationHandler.END

# --- (بخش ۵) Handlers مربوط به بازگشت و لیست کتاب‌ها ---

async def my_loans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لیست کتاب‌هایی که کاربر امانت گرفته است"""
    user_id = update.effective_user.id
    
    query = """
        SELECT l.id, b.title, l.borrow_date 
        FROM loans l
        JOIN books b ON l.book_id = b.id
        WHERE l.user_id = %s AND l.return_date IS NULL
        ORDER BY l.borrow_date DESC
    """
    results = db_query(query, (user_id,))
    
    if results:
        response_text = "📕 **کتاب‌های امانت گرفته شده توسط شما**:\n\n"
        for loan_id, title, borrow_date in results:
            response_text += (
                f"**عنوان**: {title}\n"
                f"**شماره امانت (برای بازگشت)**: `{loan_id}`\n"
                f"**تاریخ امانت**: {borrow_date.strftime('%Y/%m/%d')}\n"
                f"---------------------------------\n"
            )
        response_text += "\nبرای بازگرداندن یک کتاب، از گزینه **'↩️ بازگشت کتاب'** استفاده کنید."
    else:
        response_text = "✅ شما در حال حاضر هیچ کتابی را امانت نگرفته‌اید."
        
    await update.message.reply_text(response_text, parse_mode='Markdown', reply_markup=get_keyboard(user_id))


async def return_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند بازگشت کتاب"""
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "↩️ لطفا **شماره امانت (Loan ID)** کتابی که می‌خواهید بازگردانید را وارد کنید.\n"
        "(این شماره را می‌توانید از '📕 کتاب‌های من' پیدا کنید.)",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return RETURN_GET_LOAN_ID

async def process_return_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت Loan ID و ثبت بازگشت کتاب"""
    user_id = update.effective_user.id
    try:
        loan_id = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: شماره امانت باید یک عدد باشد. لطفا دوباره وارد کنید:")
        return RETURN_GET_LOAN_ID

    # 1. بررسی مالکیت و فعال بودن امانت
    loan_info = db_query("SELECT book_id, b.title FROM loans l JOIN books b ON l.book_id = b.id WHERE l.id = %s AND l.user_id = %s AND l.return_date IS NULL", (loan_id, user_id))
    
    if not loan_info:
        await update.message.reply_text(
            f"❌ شماره امانت `{loan_id}` یا نامعتبر است، یا قبلاً بازگردانده شده است، یا به شما تعلق ندارد.\n"
            "لطفا دوباره شماره امانت را وارد کنید:",
            parse_mode='Markdown'
        )
        return RETURN_GET_LOAN_ID
        
    book_id, title = loan_info[0]

    # 2. ثبت بازگشت و به‌روزرسانی موجودی در یک تراکنش
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # A. به‌روزرسانی return_date در جدول loans
        update_loan_query = "UPDATE loans SET return_date = CURRENT_TIMESTAMP WHERE id = %s"
        cursor.execute(update_loan_query, (loan_id,))
        
        # B. کاهش borrowed_count در جدول books
        update_book_query = "UPDATE books SET borrowed_count = borrowed_count - 1 WHERE id = %s"
        cursor.execute(update_book_query, (book_id,))
        
        conn.commit()
        
        await update.message.reply_text(
            f"✅ کتاب **{title}** با موفقیت بازگردانده شد.\n"
            "از همکاری شما متشکریم!",
            reply_markup=get_keyboard(user_id),
            parse_mode='Markdown'
        )
        
    except psycopg2.Error as e:
        logger.error(f"خطا در ثبت بازگشت (Transaction Failed): {e}")
        if conn: conn.rollback()
        await update.message.reply_text("❌ خطایی در ثبت بازگشت رخ داد. لطفا دوباره تلاش کنید.", reply_markup=get_keyboard(user_id))
        
    finally:
        if conn: conn.close()
        context.user_data.clear()
        return ConversationHandler.END


async def list_loans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش لیست تمام امانت‌های فعال (فقط ادمین)"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما اجازه دسترسی به این بخش را ندارید.", reply_markup=get_keyboard(user_id))
        return 

    # Query now includes user_id to display in the result
    query = """
        SELECT l.id, b.title, l.user_id, l.borrow_date
        FROM loans l
        JOIN books b ON l.book_id = b.id
        WHERE l.return_date IS NULL
        ORDER BY l.borrow_date ASC
    """
    results = db_query(query)
    
    if results:
        response_text = "📦 **لیست امانت‌های فعال (بازگردانده نشده)**:\n\n"
        for loan_id, title, borrower_id, borrow_date in results:
            
            # --- بهبود جدید: نمایش نام کاربری (username) ---
            # NOTE: Telegram bot API does not easily allow fetching username from ID 
            # unless the bot has interacted with the user recently. 
            # We'll rely on the user ID here, but mention how to find the username.
            
            response_text += (
                f"**عنوان**: {title}\n"
                f"**شناسه امانت**: `{loan_id}`\n"
                f"**شناسه کاربر (ID)**: `{borrower_id}`\n"
                f"**تاریخ امانت**: {borrow_date.strftime('%Y/%m/%d')}\n"
                f"---------------------------------\n"
            )
        response_text += "\nنکته: برای یافتن کاربر با استفاده از ID عددی، باید از طریق API ادمین اقدام کنید یا از لیست کاربران ربات استفاده کنید."
    else:
        response_text = "✅ در حال حاضر هیچ کتابی امانت گرفته نشده است."
        
    await update.message.reply_text(response_text, parse_mode='Markdown', reply_markup=get_keyboard(user_id))


# --- (بخش ۶) Handlers مربوط به نمایش جزئیات کتاب (جدید) ---

async def details_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند نمایش جزئیات کتاب"""
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "🔎 لطفا **ID کتابی** که می‌خواهید جزئیات آن را ببینید را وارد کنید:\n"
        "(ID را از قسمت '🔍 جستجوی کتاب' پیدا کنید.)",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return DETAILS_GET_ID

async def show_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش جزئیات کامل کتاب با ID مشخص شده"""
    user_id = update.effective_user.id
    try:
        book_id = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: ID کتاب باید یک عدد باشد. لطفا دوباره وارد کنید:")
        return DETAILS_GET_ID

    query = "SELECT title, author, subject, count, borrowed_count FROM books WHERE id = %s"
    book_info = db_query(query, (book_id,))
    
    if not book_info:
        await update.message.reply_text(f"⚠️ خطا: کتابی با ID {book_id} پیدا نشد. لطفا ID صحیح را وارد کنید:")
        return DETAILS_GET_ID

    title, author, subject, total_count, borrowed_count = book_info[0]
    borrowed_count = borrowed_count or 0
    available_count = total_count - borrowed_count
    
    response_text = (
        f"📚 **جزئیات کامل کتاب**\n"
        f"---------------------------------\n"
        f"**🆔 شناسه کتاب**: `{book_id}`\n"
        f"**📕 عنوان**: {title}\n"
        f"**✍️ نویسنده**: {author}\n"
        f"**🏷 موضوع**: {subject}\n"
        f"**🔢 موجودی کل**: {total_count}\n"
        f"**👥 تعداد امانت رفته**: {borrowed_count}\n"
        f"**⬅️ موجودی در دسترس**: **{available_count}**\n"
        f"---------------------------------"
    )
    
    await update.message.reply_text(
        response_text,
        reply_markup=get_keyboard(user_id),
        parse_mode='Markdown'
    )
    return ConversationHandler.END


# --- (بخش ۷) Handlers مربوط به حذف کتاب (جدید - فقط ادمین) ---

async def delete_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند حذف کتاب (فقط ادمین)"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما اجازه دسترسی به این بخش را ندارید.", reply_markup=get_keyboard(user_id))
        return ConversationHandler.END
        
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "🗑️ لطفا **ID کتابی** که می‌خواهید حذف کنید را وارد نمایید:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return DELETE_GET_ID

async def delete_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت ID کتاب و تأیید حذف"""
    try:
        book_id = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: ID باید یک عدد باشد. لطفا دوباره ID کتاب را وارد کنید:")
        return DELETE_GET_ID
        
    book_info = db_query("SELECT title, borrowed_count FROM books WHERE id = %s", (book_id,))
    
    if not book_info:
        await update.message.reply_text(f"⚠️ خطا: کتابی با ID {book_id} پیدا نشد. لطفا ID صحیح را وارد کنید:")
        return DELETE_GET_ID
        
    title, borrowed_count = book_info[0]
    borrowed_count = borrowed_count or 0
    
    if borrowed_count > 0:
        await update.message.reply_text(
            f"❌ کتاب **{title}** (ID: {book_id}) قابل حذف نیست، زیرا **{borrowed_count}** نسخه از آن در حال حاضر امانت رفته است.\n"
            "ابتدا باید تمام نسخه‌های امانت رفته بازگردانده شوند.",
            reply_markup=get_keyboard(update.effective_user.id),
            parse_mode='Markdown'
        )
        context.user_data.clear()
        return ConversationHandler.END
        
    context.user_data['delete_book_id'] = book_id
    context.user_data['delete_book_title'] = title
    
    confirm_keyboard = [['بله، حذف کن', 'لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        f"⚠️ **اخطار**: آیا مطمئن هستید که می‌خواهید کتاب **{title}** (ID: {book_id}) را برای همیشه حذف کنید؟",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return DELETE_CONFIRM

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """اجرای دستور حذف پس از تأیید"""
    if update.message.text == 'بله، حذف کن':
        book_id = context.user_data['delete_book_id']
        title = context.user_data['delete_book_title']
        
        # اجرای حذف کتاب (ON DELETE CASCADE در جدول loans، امانت‌های مربوطه را نیز حذف می‌کند)
        result = db_query("DELETE FROM books WHERE id = %s", (book_id,))
        
        if result is not None:
            await update.message.reply_text(
                f"✅ کتاب **{title}** (ID: {book_id}) و تمام اطلاعات امانت مرتبط با موفقیت حذف شد.",
                reply_markup=get_keyboard(update.effective_user.id),
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                "❌ خطایی در هنگام حذف کتاب رخ داد.",
                reply_markup=get_keyboard(update.effective_user.id)
            )
            
    else:
        await update.message.reply_text(
            "❌ عملیات حذف کتاب لغو شد.", 
            reply_markup=get_keyboard(update.effective_user.id)
        )

    context.user_data.clear()
    return ConversationHandler.END


# --- تابع اصلی ---

def main() -> None:
    """تابع اصلی راه‌اندازی ربات"""
    
    if not DATABASE_URL:
        logger.critical("❌❌❌ اجرای ربات متوقف شد: متغیر محیطی DATABASE_URL پیدا نشد.")
        return 
        
    init_db() 
    
    logger.info("در حال ساخت Application...")
    
    application_builder = Application.builder().token(TOKEN).concurrent_updates(True)
    application = application_builder.build()

    # --- تنظیمات Handlers ---
    
    # دستورات عمومی
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addadmin", add_admin_info)) 

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
    
    # ۴. مکالمه امانت کتاب
    borrow_book_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🤝 امانت کتاب$'), borrow_book_start)],
        states={
            BORROW_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), process_borrow_id)],
        },
        fallbacks=[MessageHandler(filters.Regex('^لغو عملیات$') | filters.COMMAND, cancel)]
    )
    
    # ۵. مکالمه بازگشت کتاب
    return_book_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^↩️ بازگشت کتاب$'), return_book_start)],
        states={
            RETURN_GET_LOAN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), process_return_id)],
        },
        fallbacks=[MessageHandler(filters.Regex('^لغو عملیات$') | filters.COMMAND, cancel)]
    )
    
    # ۶. مکالمه نمایش جزئیات کتاب (جدید)
    details_book_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔎 جزئیات کتاب$'), details_start)],
        states={
            DETAILS_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), show_details)],
        },
        fallbacks=[MessageHandler(filters.Regex('^لغو عملیات$') | filters.COMMAND, cancel)]
    )
    
    # ۷. مکالمه حذف کتاب (جدید)
    delete_book_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🗑️ حذف کتاب$'), delete_book_start)],
        states={
            DELETE_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), delete_get_id)],
            DELETE_CONFIRM: [MessageHandler(filters.Regex('^بله، حذف کن$|^لغو عملیات$'), delete_confirm)],
        },
        fallbacks=[MessageHandler(filters.COMMAND | filters.Regex('^لغو عملیات$'), cancel)]
    )


    # Handlers غیر مکالمه‌ای
    application.add_handler(MessageHandler(filters.Regex('^📕 کتاب‌های من$'), my_loans))
    application.add_handler(MessageHandler(filters.Regex('^📦 لیست امانت‌ها$'), list_loans))


    # افزودن تمام Handler ها به ربات
    application.add_handler(add_book_handler)
    application.add_handler(search_book_handler)    
    application.add_handler(edit_count_handler)     
    application.add_handler(borrow_book_handler)     
    application.add_handler(return_book_handler) 
    application.add_handler(details_book_handler)
    application.add_handler(delete_book_handler)
    
    # Handler برای پیام‌های ناشناخته 
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))
    
    # --- راه‌اندازی ربات ---
    logger.info("ربات در حال راه‌اندازی است (Polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
