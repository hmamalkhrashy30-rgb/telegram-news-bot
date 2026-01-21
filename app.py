import os
import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError
from flask import Flask, jsonify
import threading
import time
import random

# ========== إعدادات ==========
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@MarketNewsArabia')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '600'))  # 10 دقائق افتراضيًا

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN غير محدد.")

# ========== Flask App ==========
app = Flask(__name__)

# ========== قوائم الأخبار المحدثة ==========
NEWS_URLS = {
    "أخبار اقتصادية": "https://www.investing.com/news/economy",
    "أخبار الفيدرالي": "https://www.investing.com/central-banks/fed",
    "أخبار التضخم": "https://www.investing.com/economic-calendar/inflation-69",
    "أخبار الوظائف": "https://www.investing.com/economic-calendar/nonfarm-payrolls-227",
    "أخبار النفط": "https://www.investing.com/commodities/crude-oil-news",
    "أخبار الذهب": "https://www.investing.com/commodities/gold-news",
    "أخبار جيوسياسية": "https://www.investing.com/news/geopolitical-news"
}

KEYWORDS_ARABIC = {
    'فائدة': ['فائدة', 'فيدرالي', 'بنك مركزي', 'interest', 'rate', 'fed'],
    'تضخم': ['تضخم', 'أسعار', 'مستهلك', 'cpi', 'inflation'],
    'بطالة': ['بطالة', 'وظائف', 'تشغيل', 'unemployment', 'jobs', 'nfp'],
    'ناتج': ['ناتج', 'محلي', 'اقتصاد', 'نمو', 'gdp', 'growth'],
    'نفط': ['نفط', 'بترول', 'خام', 'نفطي', 'oil', 'crude', 'أوبك'],
    'ذهب': ['ذهب', 'ذهبى', 'معدن', 'gold', 'bullion'],
    'حرب': ['حرب', 'صراع', 'نزاع', 'war', 'conflict'],
    'عقوبات': ['عقوبات', 'عقوبة', 'sanctions', 'embargo']
}

sent_articles = set()
bot_started = False

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "Telegram News Bot",
        "channel": CHANNEL_USERNAME,
        "bot_started": bot_started,
        "articles_in_memory": len(sent_articles),
        "check_url": f"/check-now?token={os.getenv('RENDER_TOKEN', 'test')}"
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200

@app.route('/check-now')
def check_now():
    """فحص يدوي للأخبار"""
    token = request.args.get('token')
    if token != os.getenv('RENDER_TOKEN', 'test'):
        return jsonify({"error": "Token invalid"}), 401
    
    threading.Thread(target=run_manual_check).start()
    return jsonify({
        "message": "جاري الفحص اليدوي...",
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    })

# ========== وظائف جلب الأخبار المحدثة ==========
async def fetch_news(session, url, category):
    """جلب الأخبار باستخدام User-Agent عشوائي"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    
    headers = {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    }
    
    try:
        logging.info(f"📡 جلب الأخبار من: {category}")
        async with session.get(url, headers=headers, timeout=20, ssl=False) as response:
            if response.status == 200:
                html = await response.text()
                return await parse_investing_news(html, category)
            else:
                logging.warning(f"⚠️ حالة غير متوقعة: {response.status} لـ {category}")
                return []
    except Exception as e:
        logging.error(f"❌ خطأ في جلب {category}: {str(e)}")
        return []

async def parse_investing_news(html, category):
    """تحليل صفحة Investing.com بشكل صحيح"""
    soup = BeautifulSoup(html, 'html.parser')
    articles = []
    
    # محاولات مختلفة للعثور على الأخبار - محدث
    news_selectors = [
        'article.js-article-item',  # الشكل الجديد
        'div.largeTitle article',   # شكل آخر
        'div.mediumTitle article',  # شكل آخر
        'div[data-test="article-item"]',  # اختبار حديث
        'div.articleItem',          # شكل قديم
        'div.textDiv'               # شكل قديم جداً
    ]
    
    news_items = []
    for selector in news_selectors:
        news_items = soup.select(selector)
        if news_items:
            logging.info(f"✅ تم العثور على {len(news_items)} خبر باستخدام: {selector}")
            break
    
    if not news_items:
        # محاولة بديلة: البحث عن جميع المقالات
        all_articles = soup.find_all(['article', 'div'], class_=lambda x: x and any(word in str(x).lower() for word in ['article', 'news', 'item']))
        news_items = all_articles[:15]  # أول 15 فقط
        logging.info(f"🔍 محاولة بديلة: وجدت {len(news_items)} عنصر")
    
    for item in news_items[:12]:  # أول 12 خبر فقط
        try:
            # استخراج العنوان
            title_elem = item.find(['a', 'h3', 'div'], class_=lambda x: x and 'title' in str(x).lower())
            if not title_elem:
                title_elem = item.find('a', href=True)
            
            if not title_elem:
                continue
            
            title = title_elem.get_text(strip=True)
            if not title or len(title) < 10:
                continue
            
            # استخراج الرابط
            link = title_elem.get('href', '')
            if link and not link.startswith('http'):
                link = f"https://www.investing.com{link}"
            
            # استخراج الوقت
            time_elem = item.find('time') or item.find('span', class_=lambda x: x and any(word in str(x).lower() for word in ['time', 'date', 'ago']))
            time_text = time_elem.get_text(strip=True) if time_elem else "منذ قليل"
            
            # استخراج الملخص إن وجد
            summary_elem = item.find('p', class_=lambda x: x and any(word in str(x).lower() for word in ['summary', 'desc', 'text']))
            summary = summary_elem.get_text(strip=True)[:150] if summary_elem else ""
            
            # تصنيف الخبر
            news_type = categorize_news(title)
            
            # معرّف فريد
            import hashlib
            unique_id = hashlib.md5(f"{title[:50]}{time_text}".encode()).hexdigest()[:12]
            
            article_data = {
                'title': title,
                'link': link,
                'time': time_text,
                'summary': summary,
                'category': category,
                'type': news_type,
                'unique_id': unique_id,
                'timestamp': time.time()
            }
            
            articles.append(article_data)
            logging.debug(f"   ✓ {title[:50]}...")
            
        except Exception as e:
            logging.debug(f"   ✗ خطأ في تحليل عنصر: {e}")
            continue
    
    logging.info(f"📊 {category}: تم تحليل {len(articles)} خبر")
    return articles

def categorize_news(title):
    """تصنيف الخبر بناءً على الكلمات المفتاحية"""
    title_lower = title.lower()
    
    for news_type, keywords in KEYWORDS_ARABIC.items():
        for keyword in keywords:
            if keyword.lower() in title_lower:
                return news_type
    
    return "عام"

async def send_to_telegram(bot, article):
    """إرسال الخبر إلى قناة التليجرام"""
    try:
        emoji_map = {
            'فائدة': '🏦',
            'تضخم': '📈',
            'بطالة': '👥',
            'ناتج': '📊',
            'نفط': '🛢️',
            'ذهب': '💰',
            'حرب': '⚔️',
            'عقوبات': '🚫',
            'عام': '📰'
        }
        
        emoji = emoji_map.get(article['type'], '📰')
        
        # تنسيق الرسالة
        message = f"""
{emoji} **{article['type'].upper()}** | {article['category']}

{article['title']}

{article['summary']}

⏰ {article['time']}

🔗 [قراءة التفاصيل]({article['link']})
        """
        
        await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message[:4000],  # حد تليجرام
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logging.info(f"✅ تم إرسال: {article['title'][:40]}...")
        sent_articles.add(article['unique_id'])
        return True
        
    except Exception as e:
        logging.error(f"❌ فشل إرسال: {e}")
        return False

# ========== الدورة الرئيسية المحدثة ==========
async def news_loop():
    """الدورة الرئيسية للفحص التلقائي"""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # اختبار الاتصال بالبوت
    try:
        me = await bot.get_me()
        logging.info(f"🤖 البوت جاهز: @{me.username}")
    except Exception as e:
        logging.error(f"❌ فشل الاتصال بالبوت: {e}")
        return
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                logging.info("=" * 50)
                logging.info("🔄 بدء دورة فحص جديدة للأخبار")
                
                all_articles = []
                
                # جلب من جميع المصادر بالتتابع
                for category, url in NEWS_URLS.items():
                    try:
                        logging.info(f"⬇️  جاري: {category}")
                        articles = await fetch_news(session, url, category)
                        all_articles.extend(articles)
                        logging.info(f"   ✅ {len(articles)} خبر من {category}")
                        
                        # انتظار عشوائي بين الطلبات
                        await asyncio.sleep(random.uniform(2, 5))
                        
                    except Exception as e:
                        logging.error(f"   ❌ خطأ في {category}: {e}")
                        continue
                
                # فلترة الأخبار المهمة
                important = [a for a in all_articles if a['type'] != "عام"]
                
                # إرسال الجديد فقط
                new_count = 0
                for article in important:
                    if article['unique_id'] not in sent_articles:
                        success = await send_to_telegram(bot, article)
                        if success:
                            new_count += 1
                            await asyncio.sleep(3)  # انتظار بين الإرسال
                
                # الإحصائيات
                total_found = len(all_articles)
                total_important = len(important)
                
                logging.info("=" * 50)
                logging.info(f"📊 الإحصائيات:")
                logging.info(f"   📝 إجمالي الأخبار: {total_found}")
                logging.info(f"   ⭐ الأخبار المهمة: {total_important}")
                logging.info(f"   🆕 الجديدة المرسلة: {new_count}")
                logging.info(f"   💾 في الذاكرة: {len(sent_articles)}")
                logging.info("=" * 50)
                
                if total_found == 0:
                    logging.warning("⚠️ لم يتم العثور على أي أخبار! قد يكون هيكل الموقع تغير.")
                
            except Exception as e:
                logging.error(f"🚨 خطأ في الدورة: {e}")
            
            logging.info(f"⏳ انتظار {CHECK_INTERVAL} ثانية للفحص التالي...")
            await asyncio.sleep(CHECK_INTERVAL)

def run_manual_check():
    """تشغيل فحص يدوي"""
    async def manual_run():
        logging.info("🔍 بدء فحص يدوي...")
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        async with aiohttp.ClientSession() as session:
            # اختبار صفحة واحدة فقط للسرعة
            test_url = "https://www.investing.com/news/economy"
            articles = await fetch_news(session, test_url, "اختبار")
            
            if articles:
                logging.info(f"✅ الفحص اليدوي: وجد {len(articles)} خبر")
                for article in articles[:2]:  # أول خبرين فقط
                    await send_to_telegram(bot, article)
                    await asyncio.sleep(2)
            else:
                logging.warning("⚠️ الفحص اليدوي: لم يتم العثور على أخبار")
    
    asyncio.run(manual_run())

def start_bot():
    """بدء تشغيل البوت"""
    global bot_started
    try:
        # بدء الدورة في thread منفصل
        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(news_loop())
        
        bot_thread = threading.Thread(target=run_loop, daemon=True)
        bot_thread.start()
        
        bot_started = True
        logging.info("🚀 بوت الأخبار يعمل في الخلفية")
        
    except Exception as e:
        logging.error(f"❌ فشل بدء البوت: {e}")
        bot_started = False

# ========== نقطة الدخول ==========
if __name__ == "__main__":
    # إعداد التسجيل
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    logger = logging.getLogger(__name__)
    
    # بدء Flask في thread منفصل
    def run_flask():
        port = int(os.getenv('PORT', 10000))
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    logging.info("=" * 60)
    logging.info("🚀 بدء تشغيل بوت أخبار الأسواق المالية")
    logging.info(f"📢 القناة: {CHANNEL_USERNAME}")
    logging.info(f"⏰ فترة الفحص: {CHECK_INTERVAL} ثانية")
    logging.info(f"🌐 الخدمة: https://telegram-news-bot-ru9d.onrender.com")
    logging.info("=" * 60)
    
    # تأخير ثم بدء البوت
    time.sleep(5)
    start_bot()
    
    # إبقاء البرنامج يعمل
    try:
        while True:
            time.sleep(3600)  # ساعة
    except KeyboardInterrupt:
        logging.info("👋 إيقاف البوت...")
