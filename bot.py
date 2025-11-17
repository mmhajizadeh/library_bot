import os
import psycopg2
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, ForceReply
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from collections import defaultdict
from itertools import chain
from datetime import datetime

# --- توکن و تنظیمات ---
# This token should be read from environment variables in a real application, 
# but for this example, we keep it here.
TOKEN = "8548212605:AAHqcczpKhO9YUcJyiQbJcZ3LnqcymMRYf8"
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
# برای امانت کتاب (فقط درخواست ثبت می‌شود)
BORROW_GET_ID = 7
# برای بازگرداندن کتاب
RETURN_GET_LOAN_ID = 8 
# برای نمایش جزئیات کتاب
DETAILS_GET_ID = 9
# برای حذف کتاب
DELETE_GET_ID, DELETE_CONFIRM = range(10, 12)
# برای مرور موضوعی (جدید)
BROWSE_GET_SUBJECT_CHOICE = 12
# برای تأیید/رد درخواست امانت (جدید)
APPROVAL_GET_LOAN_ID, APPROVAL_CONFIRM_ACTION = range(13, 15)


# --- توابع کمکی دیتابیس ---

def db_query(query, params=()):
    """یک تابع کمکی برای اتصال و اجرای کوئری در دیتابیس PostgreSQL"""
    if not DATABASE_URL:
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
    """ایجاد جداول مورد نیاز برای PostgreSQL و به‌روزرسانی ساختار (Migration)"""
    if not DATABASE_URL:
        logger.error("خطا: DATABASE_URL در دسترس نیست. جداول ایجاد نشدند.")
        return
        
    logger.info("در حال بررسی و ایجاد جداول دیتابیس PostgreSQL...")
    
    # 1. جدول books
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

    # 2. جدول admins
    db_query("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY
        )
    """)
    
    # 3. جدول loans - با فیلد جدید status برای وضعیت درخواست
    db_query("""
        CREATE TABLE IF NOT EXISTS loans (
            id SERIAL PRIMARY KEY,
            book_id INTEGER REFERENCES books(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            borrow_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            return_date TIMESTAMP DEFAULT NULL,
            status TEXT DEFAULT 'PENDING' 
        )
    """)

    # افزودن ستون 'status' به جدول loans اگر وجود نداشته باشد (Migration)
    try:
        db_query("ALTER TABLE loans ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'PENDING'")
    except Exception as e:
        logger.warning(f"Failed to add 'status' column to loans table: {e}")
        
def is_admin(user_id):
    """چک می‌کند آیا کاربر ادمین است یا خیر"""
    query = "SELECT 1 FROM admins WHERE user_id = %s"
    result = db_query(query, (user_id,))
    return bool(result)

def get_admin_user_ids():
    """بازیابی لیست تمام ID های ادمین‌ها"""
    results = db_query("SELECT user_id FROM admins")
    return [r[0] for r in results] if results else []

# --- Handlers عمومی و ناوبری ---

def get_keyboard(user_id):
    """ساخت کیبورد بر اساس نقش کاربر"""
    if is_admin(user_id):
        # اضافه شدن دکمه‌های '🏷️ مرور موضوعی' و '📩 درخواست‌های امانت'
        return ReplyKeyboardMarkup([
            ['📚 افزودن کتاب', '🔍 جستجوی کتاب'],
            ['✏️ ویرایش موجودی', '🗑️ حذف کتاب'], 
            ['🔎 جزئیات کتاب', '📦 لیست امانت‌ها'], 
            ['🏷️ مرور موضوعی', '📩 درخواست‌های امانت'] 
        ], resize_keyboard=True, one_time_keyboard=False)
    else:
        # اضافه شدن دکمه '🏷️ مرور موضوعی'
        return ReplyKeyboardMarkup([
            ['🔍 جستجوی کتاب', '🤝 امانت کتاب'], 
            ['📕 کتاب‌های من', '↩️ بازگشت کتاب'],
            ['🔎 جزئیات کتاب', '🏷️ مرور موضوعی'] 
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

# --- Handlers مربوط به افزودن کتاب ---
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
        await update.message.reply_text(f"✅ کتاب **{book_data['title']}** (ID: {last_id}) با موفقیت اضافه شد.", reply_markup=get_keyboard(update.effective_user.id), parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ خطایی در ذخیره اطلاعات رخ داد.", reply_markup=get_keyboard(update.effective_user.id))
        
    context.user_data.clear()
    return ConversationHandler.END


# --- Handlers مربوط به جستجو و جزئیات ---
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
            borrowed = borrowed or 0
            available = count - borrowed 
            response_text += (
                f"**📕 {title}**\n"
                f"    🆔: {book_id}\n"
                f"    ✍️: {author}\n"
                f"    🏷: {subject}\n"
                f"    ⬅️ موجودی: {available} (از کل {count} عدد)\n"
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
    
# --- Handlers مربوط به ویرایش موجودی (ادمین) ---
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


# --- Handlers مربوط به حذف کتاب (ادمین) ---
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
    
    # علاوه بر borrowed_count، باید بررسی کنیم که آیا درخواست PENDING برای این کتاب وجود دارد یا خیر
    pending_loans_count = db_query("SELECT COUNT(*) FROM loans WHERE book_id = %s AND status = 'PENDING'", (book_id,))
    pending_loans_count = pending_loans_count[0][0] if pending_loans_count else 0


    if borrowed_count > 0:
        await update.message.reply_text(
            f"❌ کتاب **{title}** (ID: {book_id}) قابل حذف نیست، زیرا **{borrowed_count}** نسخه از آن در حال حاضر امانت رفته است.\n"
            "ابتدا باید تمام نسخه‌های امانت رفته بازگردانده شوند.",
            reply_markup=get_keyboard(update.effective_user.id),
            parse_mode='Markdown'
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    if pending_loans_count > 0:
        await update.message.reply_text(
            f"❌ کتاب **{title}** (ID: {book_id}) قابل حذف نیست، زیرا **{pending_loans_count}** درخواست امانت فعال (Pending) برای آن وجود دارد.\n"
            "ابتدا باید تمام درخواست‌ها رد یا تأیید شوند.",
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


# --- Handlers مربوط به مرور موضوعی ---

async def browse_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند مرور موضوعی: دریافت لیست موضوعات"""
    
    # بازیابی تمام موضوعات منحصر به فرد
    subjects_raw = db_query("SELECT DISTINCT subject FROM books WHERE subject IS NOT NULL ORDER BY subject ASC")
    
    if not subjects_raw:
        await update.message.reply_text("❌ متأسفانه هنوز هیچ موضوعی در کتابخانه ثبت نشده است.", 
                                         reply_markup=get_keyboard(update.effective_user.id))
        return ConversationHandler.END
        
    subjects = [s[0] for s in subjects_raw]
    
    # تقسیم موضوعات به ردیف‌های ۳ تایی برای کیبورد
    keyboard_rows = [subjects[i:i + 3] for i in range(0, len(subjects), 3)]
    keyboard_rows.append(['لغو عملیات'])
    
    reply_markup = ReplyKeyboardMarkup(keyboard_rows, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "🏷️ لطفا یکی از **موضوعات** زیر را برای مشاهده کتاب‌ها انتخاب کنید:",
        reply_markup=reply_markup
    )
    return BROWSE_GET_SUBJECT_CHOICE

async def browse_show_books(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت موضوع انتخابی و نمایش لیست کتاب‌ها"""
    subject = update.message.text
    
    # بررسی کنید که موضوع انتخاب شده در واقع یکی از موضوعات دیتابیس باشد
    subjects_check = db_query("SELECT DISTINCT subject FROM books")
    valid_subjects = [s[0] for s in subjects_check] if subjects_check else []
    
    if subject not in valid_subjects:
        await update.message.reply_text("⚠️ لطفا یک موضوع از لیست دکمه‌ها انتخاب کنید یا 'لغو عملیات' را بزنید.")
        return BROWSE_GET_SUBJECT_CHOICE

    query = """
        SELECT id, title, author, count, borrowed_count FROM books 
        WHERE subject = %s 
        ORDER BY title ASC
    """
    results = db_query(query, (subject,))
    
    if results:
        response_text = f"📚 **لیست کتاب‌ها در موضوع {subject}**:\n\n"
        
        for book_id, title, author, count, borrowed in results:
            borrowed = borrowed or 0
            available = count - borrowed 
            response_text += (
                f"**📕 {title}**\n"
                f"    🆔: {book_id}\n"
                f"    ✍️: {author}\n"
                f"    🏷: {subject}\n"
                f"    ⬅️ موجودی: {available} (از کل {count} عدد)\n"
                f"---------------------------------\n"
            )
    else:
        # این حالت نباید رخ دهد چون موضوع از دیتابیس انتخاب شده است
        response_text = f"❌ متأسفانه کتابی در موضوع **{subject}** پیدا نشد."

    await update.message.reply_text(
        response_text,
        reply_markup=get_keyboard(update.effective_user.id),
        parse_mode='Markdown'
    )
    
    return ConversationHandler.END


# --- Handlers مربوط به امانت کتاب (با سیستم درخواست) ---

async def borrow_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند امانت کتاب"""
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "🤝 لطفا **ID کتابی** که می‌خواهید امانت بگیرید را وارد کنید.\n"
        "درخواست شما برای تأیید به ادمین ارسال خواهد شد.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return BORROW_GET_ID

async def process_borrow_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بررسی ID و ثبت درخواست امانت کتاب"""
    user = update.effective_user
    user_id = user.id
    
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
    borrowed_count = borrowed_count or 0 
    available_count = total_count - borrowed_count
    
    # نکته: ما اجازه می‌دهیم درخواست ثبت شود حتی اگر موجودی صفر باشد،
    # اما اگر موجودی صفر باشد، ادمین باید آن را رد کند یا کاربر در نوبت قرار گیرد (که در این نسخه ساده‌سازی شده است).
    # فعلاً فقط چک می‌کنیم که کاربر قبلاً آن را قرض نگرفته باشد.
    
    # 2. بررسی اینکه آیا کاربر قبلاً درخواست PENDING یا APPROVED برای این کتاب ندارد
    loan_check_query = """
        SELECT id, status FROM loans 
        WHERE user_id = %s AND book_id = %s AND status IN ('PENDING', 'APPROVED')
    """
    existing_loan = db_query(loan_check_query, (user_id, book_id))
    
    if existing_loan:
        existing_status = existing_loan[0][1]
        if existing_status == 'APPROVED':
            msg = f"❌ شما قبلاً کتاب **{title}** را امانت گرفته‌اید و آن را برنگردانده‌اید."
        else: # PENDING
            msg = f"❌ شما قبلاً درخواست امانت این کتاب (**{title}**) را ثبت کرده‌اید و در انتظار تأیید ادمین است."
            
        await update.message.reply_text(msg, reply_markup=get_keyboard(user_id), parse_mode='Markdown')
        return ConversationHandler.END
    
    # 3. ثبت درخواست (status='PENDING')
    conn = None
    loan_id = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # A. ثبت ردیف جدید در جدول loans با status='PENDING'
        insert_loan_query = "INSERT INTO loans (book_id, user_id, status) VALUES (%s, %s, 'PENDING') RETURNING id"
        cursor.execute(insert_loan_query, (book_id, user_id))
        loan_id = cursor.fetchone()[0]
        
        conn.commit()
        
        # B. اطلاع‌رسانی به کاربر
        await update.message.reply_text(
            f"✅ درخواست امانت کتاب **{title}** (ID: {book_id}) با موفقیت ثبت شد.\n"
            f"شماره درخواست شما: `{loan_id}`\n"
            f"لطفا منتظر تأیید ادمین باشید.",
            reply_markup=get_keyboard(user_id),
            parse_mode='Markdown'
        )
        
        # C. اطلاع‌رسانی به ادمین‌ها
        admin_ids = get_admin_user_ids()
        admin_message = (
            f"🚨 **درخواست امانت جدید!**\n"
            f"**عنوان کتاب**: {title} (ID: {book_id})\n"
            f"**کاربر متقاضی**: {user.full_name} (@{user.username or 'ندارد'}) (ID: `{user_id}`)\n"
            f"**شماره درخواست**: `{loan_id}`\n\n"
            f"برای مدیریت، از دکمه **'📩 درخواست‌های امانت'** استفاده کنید."
        )
        for admin_id in admin_ids:
              await context.bot.send_message(chat_id=admin_id, text=admin_message, parse_mode='Markdown')
        
    except psycopg2.Error as e:
        logger.error(f"خطا در ثبت درخواست امانت (Transaction Failed): {e}")
        if conn: conn.rollback()
        await update.message.reply_text("❌ خطایی در ثبت درخواست امانت رخ داد. لطفا دوباره تلاش کنید.", reply_markup=get_keyboard(user_id))
        
    finally:
        if conn: conn.close()
        context.user_data.clear()
        return ConversationHandler.END


# --- Handlers مربوط به کتاب‌های من و بازگشت کتاب ---

async def my_loans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لیست کتاب‌هایی که کاربر امانت گرفته است (PENDING یا APPROVED)"""
    user_id = update.effective_user.id
    
    query = """
        SELECT l.id, b.title, l.borrow_date, l.status
        FROM loans l
        JOIN books b ON l.book_id = b.id
        WHERE l.user_id = %s AND l.status IN ('PENDING', 'APPROVED')
        ORDER BY l.borrow_date DESC
    """
    results = db_query(query, (user_id,))
    
    if results:
        response_text = "📕 **وضعیت کتاب‌های شما**:\n\n"
        for loan_id, title, borrow_date, status in results:
            status_fa = '✅ تأیید شده (امانت فعال)' if status == 'APPROVED' else '⏳ در انتظار تأیید ادمین'
            response_text += (
                f"**عنوان**: {title}\n"
                f"**شماره امانت/درخواست**: `{loan_id}`\n"
                f"**وضعیت**: **{status_fa}**\n"
                f"**تاریخ ثبت**: {borrow_date.strftime('%Y/%m/%d')}\n"
                f"---------------------------------\n"
            )
        response_text += "\nبرای بازگرداندن کتاب (فقط موارد تأیید شده), از گزینه **'↩️ بازگشت کتاب'** و شماره امانت آن استفاده کنید."
    else:
        response_text = "✅ شما در حال حاضر هیچ کتابی را امانت نگرفته‌اید یا درخواستی ندارید."
        
    await update.message.reply_text(response_text, parse_mode='Markdown', reply_markup=get_keyboard(user_id))


async def return_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند بازگشت کتاب"""
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "↩️ لطفا **شماره امانت (Loan ID)** کتابی که می‌خواهید بازگردانید را وارد کنید.\n"
        "(این شماره برای موارد **تأیید شده** از '📕 کتاب‌های من' قابل مشاهده است.)",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return RETURN_GET_LOAN_ID

async def process_return_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت Loan ID و ثبت بازگشت کتاب و به‌روزرسانی دیتابیس (تکمیل شده)"""
    user_id = update.effective_user.id
    
    try:
        loan_id = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: شماره امانت باید یک عدد باشد. لطفا دوباره وارد کنید:")
        return RETURN_GET_LOAN_ID

    # 1. بررسی مالکیت و فعال بودن امانت (باید APPROVED باشد)
    loan_info = db_query("""
        SELECT book_id, b.title, l.user_id 
        FROM loans l 
        JOIN books b ON l.book_id = b.id 
        WHERE l.id = %s AND l.user_id = %s AND l.status = 'APPROVED'
    """, (loan_id, user_id))
    
    if not loan_info:
        # چک کردن وضعیت‌های دیگر برای راهنمایی بهتر
        loan_check = db_query("SELECT status FROM loans WHERE id = %s AND user_id = %s", (loan_id, user_id))
        
        if loan_check and loan_check[0][0] == 'PENDING':
             await update.message.reply_text(f"❌ شماره درخواست `{loan_id}` هنوز توسط ادمین **تأیید نشده** است. صبر کنید.", parse_mode='Markdown', reply_markup=get_keyboard(user_id))
        elif loan_check and loan_check[0][0] == 'RETURNED':
             await update.message.reply_text(f"❌ شماره امانت `{loan_id}` قبلاً **بازگردانده شده** است.", parse_mode='Markdown', reply_markup=get_keyboard(user_id))
        else:
             await update.message.reply_text(f"⚠️ خطا: شماره امانت `{loan_id}` پیدا نشد یا شما اجازه بازگرداندن آن را ندارید (فقط موارد APPROVED).", parse_mode='Markdown', reply_markup=get_keyboard(user_id))
        
        return ConversationHandler.END

    book_id, title, loan_user_id = loan_info[0]
    
    # 2. شروع تراکنش برای ثبت بازگشت و به‌روزرسانی موجودی
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # A. به‌روزرسانی وضعیت امانت به 'RETURNED' و ثبت تاریخ بازگشت
        update_loan_query = "UPDATE loans SET return_date = NOW(), status = 'RETURNED' WHERE id = %s AND user_id = %s AND status = 'APPROVED'"
        cursor.execute(update_loan_query, (loan_id, user_id))
        
        # B. کاهش borrowed_count در جدول books (مطمئن می‌شویم که کمتر از صفر نشود)
        update_book_query = "UPDATE books SET borrowed_count = GREATEST(borrowed_count - 1, 0) WHERE id = %s"
        cursor.execute(update_book_query, (book_id,))
        
        conn.commit()
        
        await update.message.reply_text(
            f"✅ کتاب **{title}** (شماره امانت: {loan_id}) با موفقیت بازگردانده شد.\n"
            "از همکاری شما متشکریم.", 
            reply_markup=get_keyboard(user_id), 
            parse_mode='Markdown'
        )
        
    except psycopg2.Error as e:
        logger.error(f"خطا در ثبت بازگشت کتاب (Transaction Failed): {e}")
        if conn: conn.rollback()
        await update.message.reply_text("❌ خطایی در بازگشت کتاب رخ داد. لطفا با ادمین تماس بگیرید.", reply_markup=get_keyboard(user_id))
        
    finally:
        if conn: conn.close()
        context.user_data.clear()
        return ConversationHandler.END


# --- Handlers مربوط به لیست امانت‌ها و تأیید درخواست‌ها (جدید - فقط ادمین) ---

async def list_all_loans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لیست تمام امانت‌های فعال (APPROVED) و نمایش کاربر امانت‌گیرنده (فقط ادمین)"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما اجازه دسترسی به این بخش را ندارید.", reply_markup=get_keyboard(user_id))
        return

    query = """
        SELECT l.id, b.title, l.borrow_date, l.user_id 
        FROM loans l
        JOIN books b ON l.book_id = b.id
        WHERE l.status = 'APPROVED'
        ORDER BY l.borrow_date ASC
    """
    results = db_query(query)
    
    if results:
        response_text = "📦 **لیست امانت‌های فعال (تأیید شده)**:\n\n"
        for loan_id, title, borrow_date, loan_user_id in results:
            response_text += (
                f"**📚 عنوان**: {title}\n"
                f"**🆔 امانت**: `{loan_id}`\n"
                f"**👤 کاربر ID**: `{loan_user_id}`\n"
                f"**تاریخ امانت**: {borrow_date.strftime('%Y/%m/%d')}\n"
                f"---------------------------------\n"
            )
    else:
        response_text = "✅ در حال حاضر هیچ امانت فعالی (APPROVED) وجود ندارد."
        
    await update.message.reply_text(response_text, parse_mode='Markdown', reply_markup=get_keyboard(user_id))


async def approval_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع فرآیند تأیید/رد درخواست امانت (فقط ادمین)"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما اجازه دسترسی به این بخش را ندارید.", reply_markup=get_keyboard(user_id))
        return ConversationHandler.END
        
    # لیست درخواست‌های PENDING
    pending_query = """
        SELECT l.id, b.title, l.user_id, l.borrow_date 
        FROM loans l
        JOIN books b ON l.book_id = b.id
        WHERE l.status = 'PENDING'
        ORDER BY l.borrow_date ASC
        LIMIT 10
    """
    pending_results = db_query(pending_query)
    
    if not pending_results:
        await update.message.reply_text("✅ در حال حاضر هیچ درخواست امانت در انتظار تأیید (PENDING) وجود ندارد.", reply_markup=get_keyboard(user_id))
        return ConversationHandler.END

    response_text = "📩 **درخواست‌های امانت در انتظار تأیید**:\n"
    for loan_id, title, loan_user_id, borrow_date in pending_results:
        response_text += (
            f"---------------------------------\n"
            f"**📚 عنوان**: {title}\n"
            f"**🆔 درخواست**: `{loan_id}`\n"
            f"**👤 کاربر ID**: `{loan_user_id}`\n"
            f"**تاریخ درخواست**: {borrow_date.strftime('%Y/%m/%d %H:%M')}\n"
        )
    
    cancel_keyboard = [['لغو عملیات']]
    reply_markup = ReplyKeyboardMarkup(cancel_keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    response_text += "\n---------------------------------\n"
    response_text += "لطفا **ID درخواست** مورد نظر برای تأیید یا رد را وارد کنید:"

    await update.message.reply_text(response_text, reply_markup=reply_markup, parse_mode='Markdown')
    return APPROVAL_GET_LOAN_ID

async def approval_get_loan_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت ID درخواست، بررسی اعتبار و نمایش دکمه‌های تأیید/رد"""
    try:
        loan_id = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ خطا: ID درخواست باید یک عدد باشد. لطفا دوباره ID صحیح را وارد کنید:")
        return APPROVAL_GET_LOAN_ID
        
    # بررسی اینکه درخواست وجود دارد و وضعیت آن PENDING است
    query = """
        SELECT l.book_id, b.title, b.count, b.borrowed_count, l.user_id 
        FROM loans l
        JOIN books b ON l.book_id = b.id
        WHERE l.id = %s AND l.status = 'PENDING'
    """
    loan_info = db_query(query, (loan_id,))
    
    if not loan_info:
        await update.message.reply_text(f"⚠️ خطا: درخواست `{loan_id}` پیدا نشد، یا وضعیت آن PENDING نیست.", parse_mode='Markdown')
        return APPROVAL_GET_LOAN_ID
        
    book_id, title, total_count, borrowed_count, user_id = loan_info[0]
    borrowed_count = borrowed_count or 0
    available_count = total_count - borrowed_count
    
    context.user_data['approval_loan_id'] = loan_id
    context.user_data['approval_book_id'] = book_id
    context.user_data['approval_user_id'] = user_id
    context.user_data['approval_book_title'] = title
    
    status_msg = ""
    if available_count <= 0:
        status_msg = "\n\n⚠️ **اخطار**: موجودی در دسترس این کتاب **صفر** است. با تأیید این درخواست، موجودی امانت رفته افزایش یافته و موجودی در دسترس منفی می‌شود."
        
    confirm_keyboard = [
        ['✅ تأیید امانت', '❌ رد درخواست'],
        ['لغو عملیات']
    ]
    reply_markup = ReplyKeyboardMarkup(confirm_keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        f"**جزئیات درخواست امانت** (ID: `{loan_id}`):\n"
        f"**کتاب**: {title}\n"
        f"**کاربر متقاضی ID**: `{user_id}`\n"
        f"**موجودی در دسترس کنونی**: {available_count} (از کل {total_count})\n"
        f"{status_msg}"
        f"\nلطفا اقدام مورد نظر را انتخاب کنید:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return APPROVAL_CONFIRM_ACTION

async def approval_confirm_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """اجرای تأیید یا رد درخواست امانت و اطلاع‌رسانی به کاربر"""
    action = update.message.text
    loan_id = context.user_data.get('approval_loan_id')
    book_id = context.user_data.get('approval_book_id')
    loan_user_id = context.user_data.get('approval_user_id')
    title = context.user_data.get('approval_book_title')
    
    if action == '✅ تأیید امانت':
        new_status = 'APPROVED'
        
        conn = None
        try:
            conn = psycopg2.connect(DATABASE_URL)
            cursor = conn.cursor()
            
            # 1. به‌روزرسانی وضعیت امانت و ثبت تاریخ امانت
            update_loan_query = "UPDATE loans SET status = %s, borrow_date = NOW() WHERE id = %s"
            cursor.execute(update_loan_query, (new_status, loan_id))
            
            # 2. افزایش borrowed_count در جدول books
            update_book_query = "UPDATE books SET borrowed_count = borrowed_count + 1 WHERE id = %s"
            cursor.execute(update_book_query, (book_id,))
            
            conn.commit()
            
            # اطلاع‌رسانی به ادمین
            await update.message.reply_text(
                f"✅ درخواست امانت `{loan_id}` برای کتاب **{title}** تأیید و امانت ثبت شد.",
                reply_markup=get_keyboard(update.effective_user.id),
                parse_mode='Markdown'
            )
            
            # اطلاع‌رسانی به کاربر متقاضی
            try:
                await context.bot.send_message(
                    chat_id=loan_user_id, 
                    text=f"🎉 **درخواست امانت شما تأیید شد!**\nکتاب **{title}** با شماره امانت `{loan_id}` اکنون رسماً در امانت شماست. لطفا در اسرع وقت برای دریافت کتاب اقدام کنید.",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.warning(f"Failed to send confirmation to user {loan_user_id}: {e}")
                
        except psycopg2.Error as e:
            logger.error(f"خطا در تأیید امانت (Transaction Failed): {e}")
            if conn: conn.rollback()
            await update.message.reply_text("❌ خطایی در ثبت تأیید امانت رخ داد. عملیات لغو شد.", reply_markup=get_keyboard(update.effective_user.id))
            
        finally:
            if conn: conn.close()
            
    elif action == '❌ رد درخواست':
        new_status = 'REJECTED'
        # 1. به‌روزرسانی وضعیت امانت به 'REJECTED'
        db_query("UPDATE loans SET status = %s WHERE id = %s", (new_status, loan_id))
        
        # اطلاع‌رسانی به ادمین
        await update.message.reply_text(
            f"❌ درخواست امانت `{loan_id}` برای کتاب **{title}** رد شد.",
            reply_markup=get_keyboard(update.effective_user.id),
            parse_mode='Markdown'
        )
        
        # اطلاع‌رسانی به کاربر متقاضی
        try:
            await context.bot.send_message(
                chat_id=loan_user_id, 
                text=f"💔 **درخواست امانت شما رد شد.**\nمتأسفانه درخواست امانت شما برای کتاب **{title}** با شماره `{loan_id}` توسط ادمین رد شد.",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.warning(f"Failed to send rejection to user {loan_user_id}: {e}")

    else:
        await update.message.reply_text(
            "❌ عملیات تأیید/رد درخواست لغو شد.", 
            reply_markup=get_keyboard(update.effective_user.id)
        )
        
    context.user_data.clear()
    return ConversationHandler.END


def main() -> None:
    """تابع اصلی ربات"""
    # 1. آماده‌سازی دیتابیس
    init_db()
    
    # 2. ساخت اپلیکیشن
    application = Application.builder().token(TOKEN).build()

    # 3. تعریف Conversation Handlers
    
    # افزودن کتاب (Admin)
    add_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📚 افزودن کتاب$"), add_book_start)],
        states={
            GET_TITLE: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), get_title)],
            GET_AUTHOR: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), get_author)],
            GET_SUBJECT: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), get_subject)],
            GET_COUNT: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), get_count)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )

    # جستجو
    search_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 جستجوی کتاب$"), search_start)],
        states={
            SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), execute_search)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )

    # ویرایش موجودی (Admin)
    edit_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✏️ ویرایش موجودی$"), edit_count_start)],
        states={
            EDIT_GET_ID: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), get_book_id_for_edit)],
            EDIT_GET_NEW_COUNT: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), get_new_count)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )

    # حذف کتاب (Admin)
    delete_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🗑️ حذف کتاب$"), delete_book_start)],
        states={
            DELETE_GET_ID: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), delete_get_id)],
            DELETE_CONFIRM: [MessageHandler(filters.Regex("^(بله، حذف کن|لغو عملیات)$"), delete_confirm)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )

    # امانت کتاب (User - ثبت درخواست)
    borrow_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🤝 امانت کتاب$"), borrow_book_start)],
        states={
            BORROW_GET_ID: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), process_borrow_id)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )

    # بازگشت کتاب (User)
    return_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^↩️ بازگشت کتاب$"), return_book_start)],
        states={
            RETURN_GET_LOAN_ID: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), process_return_id)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )
    
    # مرور موضوعی (همه)
    browse_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🏷️ مرور موضوعی$"), browse_start)],
        states={
            BROWSE_GET_SUBJECT_CHOICE: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), browse_show_books)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )
    
    # مدیریت درخواست‌های امانت (Admin - جدید)
    approval_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📩 درخواست‌های امانت$"), approval_start)],
        states={
            APPROVAL_GET_LOAN_ID: [MessageHandler(filters.TEXT & ~filters.Regex("^(لغو عملیات)$"), approval_get_loan_id)],
            APPROVAL_CONFIRM_ACTION: [MessageHandler(filters.Regex("^(✅ تأیید امانت|❌ رد درخواست|لغو عملیات)$"), approval_confirm_action)],
        },
        fallbacks=[MessageHandler(filters.Regex("^لغو عملیات$"), cancel)],
    )


    # 4. افزودن Handlers به اپلیکیشن
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addadmin", add_admin_info))

    application.add_handler(add_conv_handler)
    application.add_handler(search_conv_handler)
    application.add_handler(edit_conv_handler)
    application.add_handler(delete_conv_handler)
    application.add_handler(borrow_conv_handler)
    application.add_handler(return_conv_handler)
    application.add_handler(browse_conv_handler)
    application.add_handler(approval_conv_handler) # Handler جدید برای تأیید/رد درخواست‌ها

    # Handlers پیام‌های مستقیم (Non-Conversation)
    application.add_handler(MessageHandler(filters.Regex("^📕 کتاب‌های من$"), my_loans))
    application.add_handler(MessageHandler(filters.Regex("^📦 لیست امانت‌ها$"), list_all_loans)) # Handler جدید برای لیست امانت‌ها

    # 5. شروع ربات
    logger.info("ربات در حال اجرا است...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
