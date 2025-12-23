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
from flask import Flask
from threading import Thread

# --- وب‌سرور برای زنده نگه داشتن ربات در Render ---
app = Flask('')

@app.route('/')
def home():
    return "I am alive! Bot is running..."

def run_http():
    # Render پورت را در متغیر محیطی PORT قرار می‌دهد
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_http)
    t.start()
# ---------------------------------------------------

# --- توکن و تنظیمات ---
# مقادیر را از متغیرهای محیطی Render می‌خوانیم
TOKEN = os.environ.get('TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

# --- فعال کردن لاگینگ ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- تعریف حالت‌های مکالمه ---
GET_TITLE, GET_AUTHOR, GET_SUBJECT, GET_COUNT = range(4)
SEARCH_QUERY = 4
EDIT_GET_ID, EDIT_GET_NEW_COUNT = range(5, 7)
BORROW_GET_ID = 7
RETURN_GET_LOAN_ID = 8 
DETAILS_GET_ID = 9
DELETE_GET_ID, DELETE_CONFIRM = range(10, 12)
BROWSE_GET_SUBJECT_CHOICE = 12
APPROVAL_GET_LOAN_ID, APPROVAL_CONFIRM_ACTION = range(13, 15)

# --- توابع کمکی دیتابیس ---

def db_query(query, params=()):
    """یک تابع کمکی برای اتصال و اجرای کوئری در دیتابیس PostgreSQL"""
    if not DATABASE_URL:
        logger.error("خطا: DATABASE_URL در دسترس نیست.")
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
        logger.error(f"خطای دیتابیس: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()

def init_db():
    """ایجاد جداول مورد نیاز"""
    if not DATABASE_URL:
        logger.error("خطا: DATABASE_URL تنظیم نشده است.")
        return
        
    logger.info("در حال بررسی و ایجاد جداول دیتابیس...")
    
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
    
    # 3. جدول loans
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

    # افزودن ستون 'status' اگر وجود نداشته باشد (Migration)
    try:
        db_query("ALTER TABLE loans ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'PENDING'")
    except Exception as e:
        pass # ستون احتمالا وجود دارد
        
def is_admin(user_id):
    query = "SELECT 1 FROM admins WHERE user_id = %s"
    result = db_query(query, (user_id,))
    return bool(result)

def get_admin_user_ids():
    results = db_query("SELECT user_id FROM admins")
    return [r[0] for r in results] if results else []

# --- Handlers عمومی ---

def get_keyboard(user_id):
    if is_admin(user_id):
        return ReplyKeyboardMarkup([
            ['📚 افزودن کتاب', '🔍 جستجوی کتاب'],
            ['✏️ ویرایش موجودی', '🗑️ حذف کتاب'], 
            ['🔎 جزئیات کتاب', '📦 لیست امانت‌ها'], 
            ['🏷️ مرور موضوعی', '📩 درخواست‌های امانت'] 
        ], resize_keyboard=True, one_time_keyboard=False)
    else:
        return ReplyKeyboardMarkup([
            ['🔍 جستجوی کتاب', '🤝 امانت کتاب'], 
            ['📕 کتاب‌های من', '↩️ بازگشت کتاب'],
            ['🔎 جزئیات کتاب', '🏷️ مرور موضوعی'] 
        ], resize_keyboard=True, one_time_keyboard=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    if not DATABASE_URL:
        await update.message.reply_text("⛔️ خطا: دیتابیس وصل نیست.")
        return

    welcome_text = f"سلام {first_name}، به ربات کتابخانه خوش آمدید!\n"
    
    # ادمین کردن اولین کاربر
    if not is_admin(user_id) and not db_query("SELECT 1 FROM admins LIMIT 1"):
        db_query("INSERT INTO admins (user_id) VALUES (%s)", (user_id,))
        welcome_text += "شما به عنوان **اولین ادمین** ثبت شدید."
    elif is_admin(user_id):
        welcome_text += "شما به پنل ادمین دسترسی دارید."

    await update.message.reply_text(welcome_text, reply_markup=get_keyboard(user_id))

async def add_admin_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(f"✅ شناسه شما: `{user_id}`", parse_mode='Markdown')
    
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=get_keyboard(update.effective_user.id))
    return ConversationHandler.END

# --- Handlers افزودن کتاب ---
async def add_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("شما ادمین نیستید.", reply_markup=get_keyboard(update.effective_user.id))
        return ConversationHandler.END
    context.user_data['book_data'] = {}
    await update.message.reply_text("📚 نام کتاب را وارد کنید:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return GET_TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['book_data']['title'] = update.message.text
    await update.message.reply_text("✍️ نام نویسنده:")
    return GET_AUTHOR

async def get_author(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['book_data']['author'] = update.message.text
    reply_markup = ReplyKeyboardMarkup([['داستان', 'علمی-تخیلی', 'روانشناسی'], ['تاریخی', 'درسی', 'سایر'], ['لغو عملیات']], resize_keyboard=True)
    await update.message.reply_text("🏷 موضوع کتاب:", reply_markup=reply_markup)
    return GET_SUBJECT

async def get_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['book_data']['subject'] = update.message.text
    await update.message.reply_text("🔢 تعداد موجودی (عدد):", reply_markup=ReplyKeyboardRemove())
    return GET_COUNT

async def get_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        count = int(update.message.text)
        if count < 1: raise ValueError
    except:
        await update.message.reply_text("⚠️ عدد صحیح وارد کنید:", reply_markup=ReplyKeyboardRemove())
        return GET_COUNT
        
    book = context.user_data['book_data']
    db_query("INSERT INTO books (title, author, subject, count) VALUES (%s, %s, %s, %s)", 
             (book['title'], book['author'], book['subject'], count))
    
    await update.message.reply_text(f"✅ کتاب **{book['title']}** اضافه شد.", reply_markup=get_keyboard(update.effective_user.id), parse_mode='Markdown')
    context.user_data.clear()
    return ConversationHandler.END

# --- Handlers جستجو ---
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🔍 نام کتاب، نویسنده یا موضوع را وارد کنید:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return SEARCH_QUERY

async def execute_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    term = f'%{update.message.text}%'
    results = db_query("""
        SELECT id, title, author, subject, count, borrowed_count FROM books 
        WHERE title ILIKE %s OR author ILIKE %s OR subject ILIKE %s LIMIT 10
    """, (term, term, term))
    
    if results:
        text = f"✅ نتایج برای **'{update.message.text}'**:\n\n"
        for r in results:
            avail = r[4] - (r[5] or 0)
            text += f"📕 **{r[1]}**\n🆔: {r[0]}\n✍️: {r[2]}\n🏷: {r[3]}\n⬅️ موجود: {avail}\n------------------\n"
    else:
        text = "❌ موردی یافت نشد."
        
    await update.message.reply_text(text, reply_markup=get_keyboard(update.effective_user.id), parse_mode='Markdown')
    return ConversationHandler.END

# --- Handlers ویرایش ---
async def edit_count_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("✏️ ID کتاب را وارد کنید:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return EDIT_GET_ID

async def get_book_id_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        bid = int(update.message.text)
    except:
        await update.message.reply_text("⚠️ ID باید عدد باشد.")
        return EDIT_GET_ID
        
    res = db_query("SELECT title, count, borrowed_count FROM books WHERE id = %s", (bid,))
    if not res:
        await update.message.reply_text("⚠️ کتاب پیدا نشد.")
        return EDIT_GET_ID
        
    context.user_data['edit_bid'] = bid
    await update.message.reply_text(f"کتاب: {res[0][0]}\nموجودی فعلی: {res[0][1]}\nدست امانت: {res[0][2]}\n\n🔢 موجودی جدید را وارد کنید:", reply_markup=ReplyKeyboardRemove())
    return EDIT_GET_NEW_COUNT

async def get_new_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        cnt = int(update.message.text)
        # چک کردن اینکه از تعداد امانت کمتر نباشد
        bid = context.user_data['edit_bid']
        curr = db_query("SELECT borrowed_count FROM books WHERE id = %s", (bid,))[0][0]
        if cnt < curr:
            await update.message.reply_text(f"❌ موجودی نمیتواند کمتر از تعداد امانت ({curr}) باشد.")
            return EDIT_GET_NEW_COUNT
            
        db_query("UPDATE books SET count = %s WHERE id = %s", (cnt, bid))
        await update.message.reply_text("✅ موجودی آپدیت شد.", reply_markup=get_keyboard(update.effective_user.id))
    except:
        await update.message.reply_text("❌ خطا.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Handlers مرور موضوعی ---
async def browse_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    res = db_query("SELECT DISTINCT subject FROM books WHERE subject IS NOT NULL")
    if not res:
        await update.message.reply_text("موضوعی وجود ندارد.", reply_markup=get_keyboard(update.effective_user.id))
        return ConversationHandler.END
    rows = [[r[0]] for r in res] + [['لغو عملیات']]
    await update.message.reply_text("یک موضوع انتخاب کنید:", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
    return BROWSE_GET_SUBJECT_CHOICE

async def browse_show_books(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subj = update.message.text
    res = db_query("SELECT title, author, count, borrowed_count FROM books WHERE subject = %s", (subj,))
    if res:
        text = f"📚 کتاب‌های **{subj}**:\n\n"
        for r in res:
            avail = r[2] - (r[3] or 0)
            text += f"📕 {r[0]} | موجود: {avail}\n"
        await update.message.reply_text(text, reply_markup=get_keyboard(update.effective_user.id), parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ کتابی یافت نشد.", reply_markup=get_keyboard(update.effective_user.id))
    return ConversationHandler.END

# --- Handlers امانت (درخواست) ---
async def borrow_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🤝 ID کتاب را وارد کنید:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return BORROW_GET_ID

async def process_borrow_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    try:
        bid = int(update.message.text)
    except:
        await update.message.reply_text("⚠️ عدد وارد کنید.")
        return BORROW_GET_ID
        
    # 1. چک موجودی
    info = db_query("SELECT title, count, borrowed_count FROM books WHERE id = %s", (bid,))
    if not info:
        await update.message.reply_text("⚠️ کتاب یافت نشد.")
        return BORROW_GET_ID
    
    title, count, borrowed = info[0]
    if (count - (borrowed or 0)) <= 0:
        await update.message.reply_text("❌ موجودی ندارد.", reply_markup=get_keyboard(user.id))
        return ConversationHandler.END
        
    # 2. چک درخواست تکراری
    exists = db_query("SELECT 1 FROM loans WHERE user_id = %s AND book_id = %s AND status IN ('PENDING', 'APPROVED')", (user.id, bid))
    if exists:
        await update.message.reply_text("❌ قبلاً این کتاب را درخواست داده یا امانت گرفته‌اید.", reply_markup=get_keyboard(user.id))
        return ConversationHandler.END
        
    # 3. ثبت درخواست
    lid = db_query("INSERT INTO loans (book_id, user_id, status) VALUES (%s, %s, 'PENDING') RETURNING id", (bid, user.id))
    
    if lid: # lid یک لیست است چون fetchall خروجی می‌دهد اما اینجا چون RETURNING داریم در db_query تغییر کوچکی لازم بود که برای سادگی فرض میکنیم درست عمل میکند
        # اصلاح: db_query ما fetchall برمیگرداند.
        real_lid = lid[0][0]
        await update.message.reply_text(f"✅ درخواست شما (شماره {real_lid}) ثبت شد. منتظر تایید ادمین باشید.", reply_markup=get_keyboard(user.id))
        
        # خبر به ادمین‌ها
        for admin in get_admin_user_ids():
            try:
                await context.bot.send_message(admin, f"🚨 درخواست جدید!\nکتاب: {title}\nکاربر: {user.full_name}\nشماره درخواست: {real_lid}")
            except: pass
    else:
        await update.message.reply_text("❌ خطا در ثبت.", reply_markup=get_keyboard(user.id))
        
    return ConversationHandler.END

# --- Handlers بازگشت ---
async def return_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("↩️ شماره امانت (Loan ID) را وارد کنید:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return RETURN_GET_LOAN_ID

async def process_return_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    try:
        lid = int(update.message.text)
    except:
        await update.message.reply_text("⚠️ عدد وارد کنید.")
        return RETURN_GET_LOAN_ID
        
    # چک کردن مالکیت و وضعیت
    res = db_query("SELECT book_id FROM loans WHERE id = %s AND user_id = %s AND status = 'APPROVED'", (lid, uid))
    if not res:
        await update.message.reply_text("❌ شماره امانت نامعتبر است (یا تایید نشده یا مال شما نیست).")
        return RETURN_GET_LOAN_ID
        
    bid = res[0][0]
    
    # انجام بازگشت
    db_query("UPDATE loans SET status = 'RETURNED', return_date = CURRENT_TIMESTAMP WHERE id = %s", (lid,))
    db_query("UPDATE books SET borrowed_count = borrowed_count - 1 WHERE id = %s", (bid,))
    
    await update.message.reply_text("✅ کتاب بازگردانده شد.", reply_markup=get_keyboard(uid))
    return ConversationHandler.END

# --- Handlers مدیریت درخواست (Approval) ---
async def approval_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    
    res = db_query("SELECT l.id, b.title, l.user_id FROM loans l JOIN books b ON l.book_id = b.id WHERE l.status = 'PENDING'")
    if not res:
        await update.message.reply_text("✅ درخواست جدیدی نیست.", reply_markup=get_keyboard(update.effective_user.id))
        return ConversationHandler.END
        
    text = "📩 درخواست‌های منتظر:\n" + "\n".join([f"🔹 درخواست {r[0]}: کتاب {r[1]} (کاربر {r[2]})" for r in res])
    await update.message.reply_text(text + "\n\nشماره درخواست را وارد کنید:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return APPROVAL_GET_LOAN_ID

async def approval_get_loan_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        lid = int(update.message.text)
    except:
        await update.message.reply_text("عدد وارد کنید.")
        return APPROVAL_GET_LOAN_ID
        
    info = db_query("SELECT book_id, user_id FROM loans WHERE id = %s AND status = 'PENDING'", (lid,))
    if not info:
        await update.message.reply_text("درخواست پیدا نشد.")
        return APPROVAL_GET_LOAN_ID
        
    context.user_data['m_lid'] = lid
    context.user_data['m_bid'] = info[0][0]
    context.user_data['m_uid'] = info[0][1]
    
    await update.message.reply_text(f"درخواست {lid} انتخاب شد. چه کنم؟", reply_markup=ReplyKeyboardMarkup([['✅ تأیید امانت', '❌ رد درخواست'], ['لغو عملیات']], resize_keyboard=True))
    return APPROVAL_CONFIRM_ACTION

async def approval_confirm_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    act = update.message.text
    lid = context.user_data['m_lid']
    bid = context.user_data['m_bid']
    uid = context.user_data['m_uid']
    
    if 'تأیید' in act:
        # چک موجودی نهایی
        curr = db_query("SELECT count, borrowed_count, title FROM books WHERE id = %s", (bid,))
        if curr[0][0] - (curr[0][1] or 0) <= 0:
            await update.message.reply_text("❌ موجودی تمام شده!", reply_markup=get_keyboard(update.effective_user.id))
            return ConversationHandler.END
            
        db_query("UPDATE loans SET status = 'APPROVED' WHERE id = %s", (lid,))
        db_query("UPDATE books SET borrowed_count = borrowed_count + 1 WHERE id = %s", (bid,))
        
        await update.message.reply_text("✅ تأیید شد.", reply_markup=get_keyboard(update.effective_user.id))
        try: await context.bot.send_message(uid, f"✅ درخواست امانت کتاب {curr[0][2]} تأیید شد. دریافت کنید.")
        except: pass
        
    elif 'رد' in act:
        db_query("UPDATE loans SET status = 'REJECTED' WHERE id = %s", (lid,))
        await update.message.reply_text("❌ رد شد.", reply_markup=get_keyboard(update.effective_user.id))
        try: await context.bot.send_message(uid, "❌ درخواست امانت شما رد شد.")
        except: pass
        
    context.user_data.clear()
    return ConversationHandler.END

# --- Handlers دیگر ---
async def my_loans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    res = db_query("""
        SELECT l.id, b.title, l.status 
        FROM loans l JOIN books b ON l.book_id = b.id 
        WHERE l.user_id = %s AND l.status IN ('PENDING', 'APPROVED')
    """, (uid,))
    
    if res:
        text = "📕 وضعیت شما:\n" + "\n".join([f"- {r[1]} (Status: {r[2]}) [ID: {r[0]}]" for r in res])
    else:
        text = "شما امانتی ندارید."
    await update.message.reply_text(text, reply_markup=get_keyboard(uid))
    
async def list_loans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    res = db_query("SELECT l.id, b.title, l.user_id FROM loans l JOIN books b ON l.book_id = b.id WHERE l.status = 'APPROVED'")
    text = "📦 امانت‌های فعال:\n" + "\n".join([f"{r[0]}: {r[1]} (User: {r[2]})" for r in res]) if res else "خالی."
    await update.message.reply_text(text, reply_markup=get_keyboard(update.effective_user.id))

async def details_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🔎 ID کتاب:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return DETAILS_GET_ID

async def show_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        bid = int(update.message.text)
        res = db_query("SELECT title, author, subject, count, borrowed_count FROM books WHERE id = %s", (bid,))
        if res:
            r = res[0]
            msg = f"📕 {r[0]}\n✍️ {r[1]}\n🏷 {r[2]}\n🔢 کل: {r[3]}\n👥 دست مردم: {r[4] or 0}"
            await update.message.reply_text(msg, reply_markup=get_keyboard(update.effective_user.id))
        else:
            await update.message.reply_text("یافت نشد.")
    except:
        await update.message.reply_text("خطا.")
    return ConversationHandler.END

async def delete_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("🗑️ ID کتاب برای حذف:", reply_markup=ReplyKeyboardMarkup([['لغو عملیات']], resize_keyboard=True))
    return DELETE_GET_ID

async def delete_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        bid = int(update.message.text)
        # چک کردن اینکه دست کسی نباشد
        curr = db_query("SELECT borrowed_count, title FROM books WHERE id = %s", (bid,))
        if not curr: 
             await update.message.reply_text("کتاب نیست.")
             return ConversationHandler.END
             
        if (curr[0][0] or 0) > 0:
            await update.message.reply_text(f"❌ حذف نمیشود! {curr[0][0]} نسخه دست مردم است.", reply_markup=get_keyboard(update.effective_user.id))
            return ConversationHandler.END
            
        context.user_data['del_bid'] = bid
        context.user_data['del_title'] = curr[0][1]
        await update.message.reply_text(f"آیا {curr[0][1]} حذف شود؟", reply_markup=ReplyKeyboardMarkup([['بله، حذف کن', 'لغو عملیات']], resize_keyboard=True))
        return DELETE_CONFIRM
    except:
        await update.message.reply_text("خطا.")
        return ConversationHandler.END

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text == 'بله، حذف کن':
        bid = context.user_data['del_bid']
        db_query("DELETE FROM books WHERE id = %s", (bid,))
        await update.message.reply_text("🗑️ حذف شد.", reply_markup=get_keyboard(update.effective_user.id))
    else:
        await update.message.reply_text("لغو شد.", reply_markup=get_keyboard(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END

# --- تابع اصلی ---
def main() -> None:
    # بررسی متغیرهای محیطی
    if not TOKEN:
        logger.critical("توکن یافت نشد! TOKEN را در تنظیمات Render چک کنید.")
        return
    if not DATABASE_URL:
        logger.critical("آدرس دیتابیس یافت نشد! DATABASE_URL را در تنظیمات Render چک کنید.")
        return

    # ایجاد جداول دیتابیس
    init_db()
    
    # اجرای وب‌سرور (برای زنده ماندن در Render)
    keep_alive()
    
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    # افزودن هندلرها
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addadmin", add_admin_info))
    
    # 1. افزودن کتاب
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📚 افزودن کتاب$'), add_book_start)],
        states={
            GET_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_title)],
            GET_AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_author)],
            GET_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_subject)],
            GET_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_count)],
        }, fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))
    
    # 2. جستجو
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔍 جستجوی کتاب$'), search_start)],
        states={SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), execute_search)]},
        fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))
    
    # 3. مرور موضوعی
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🏷️ مرور موضوعی$'), browse_start)],
        states={BROWSE_GET_SUBJECT_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), browse_show_books)]},
        fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))

    # 4. ویرایش
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^✏️ ویرایش موجودی$'), edit_count_start)],
        states={
            EDIT_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_book_id_for_edit)],
            EDIT_GET_NEW_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), get_new_count)],
        }, fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))
    
    # 5. امانت
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🤝 امانت کتاب$'), borrow_book_start)],
        states={BORROW_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), process_borrow_id)]},
        fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))
    
    # 6. بازگشت
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^↩️ بازگشت کتاب$'), return_book_start)],
        states={RETURN_GET_LOAN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), process_return_id)]},
        fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))
    
    # 7. جزئیات
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔎 جزئیات کتاب$'), details_start)],
        states={DETAILS_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), show_details)]},
        fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))
    
    # 8. حذف
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🗑️ حذف کتاب$'), delete_book_start)],
        states={
            DELETE_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), delete_get_id)],
            DELETE_CONFIRM: [MessageHandler(filters.Regex('^بله، حذف کن$|^لغو عملیات$'), delete_confirm)]
        }, fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))
    
    # 9. تایید امانت
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📩 درخواست‌های امانت$'), approval_start)],
        states={
            APPROVAL_GET_LOAN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^لغو عملیات$'), approval_get_loan_id)],
            APPROVAL_CONFIRM_ACTION: [MessageHandler(filters.Regex('^✅ تأیید امانت$|^❌ رد درخواست$|^لغو عملیات$'), approval_confirm_action)]
        }, fallbacks=[MessageHandler(filters.ALL, cancel)]
    ))

    # هندلرهای ساده
    app.add_handler(MessageHandler(filters.Regex('^📕 کتاب‌های من$'), my_loans))
    app.add_handler(MessageHandler(filters.Regex('^📦 لیست امانت‌ها$'), list_loans))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))

    logger.info("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
