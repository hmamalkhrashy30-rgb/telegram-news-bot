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

# ========== إعدادات من متغيرات البيئة ==========
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@MarketNewsArabia')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN غير محدد.")

# ========== Flask App ==========
app = Flask(__name__)

# ========== نفس كود جلب الأخبار ==========
NEWS_URLS = {
    "اقتصادية": "https://www.investing.com/news/economic-indicators",
    "فيدرالي": "https://www.investing.com/news/fed-news",
    "تضخم": "https://www.investing.com/news/inflation-news",
    "وظائف": "https://www.investing.com/news/employment-news",
    "نفط": "https://www.investing.com/news/commodities-news",
    "ذهب": "https://www.investing.com/news/gold-news",
    "جيوسياسية": "https://www.investing.com/news/geopolitical-news"
}

KEYWORDS = {
    'فائدة': ['interest rate', 'fed', 'central bank', 'فائدة', 'بنك مركزي'],
    'تضخم': ['cpi', 'inflation', 'تضخم', 'أسعار'],
    'بطالة': ['unemployment', 'jobs', 'nfp', 'بطالة', 'وظائف'],
    'ناتج': ['gdp', 'growth', 'ناتج', 'اقتصاد'],
    'نفط': ['oil', 'crude', 'بترول', 'نفط', 'أوبك'],
    'ذهب': ['gold', 'ذهب', 'معادن'],
    'حرب': ['war', 'conflict', 'حرب', 'صراع'],
    'عقوبات': ['sanctions', 'عقوبات']
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
        "articles_sent": len(sent_articles)
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/check-now')
def check_now_manual():
    """فحص يدوي للأخبار"""
    if not bot_started:
        return jsonify({"error": "البوت لم يبدأ بعد"}), 400
    threading.Thread(target=run_once_check).start()
    return jsonify({"message": "جاري الفحص اليدوي الآن..."})

# ========== وظائف جلب الأخبار ==========
async def fetch_news(session, url, category):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'ar,en;q=0.9'
    }
    
    try:
        async with session.get(url, headers=headers, timeout=15) as response:
            if response.status == 200:
                html = await response.text()
                return parse_news(html, category)
    except Exception as e:
        logging.error(f"خطأ في جلب أخبار {category}: {e}")
    return []

def parse_news(html, category):
    soup = BeautifulSoup(html, 'html.parser')
    articles = []
    
    # محاولات مختلفة للعثور على الأخبار
    selectors = [
        'article.js-article-item',
        'div.mediumTitle1 article',
        'div.largeTitle article',
        'div[class*="articleItem"]',
        'div.textDiv'
    ]
    
    for selector in selectors:
        news_items = soup.select(selector)
        if news_items:
            break
    
    for item in news_items[:8]:  # أول 8 أخبار فقط
        try:
            # العثور على العنوان
            title_elem = item.find('a', class_='title') or item.find('a', href=True)
            if not title_elem:
                continue
                
            title = title_elem.text.strip()
            link = title_elem.get('href', '')
            
            if link and not link.startswith('http'):
                link = f"https://www.investing.com{link}"
            
            # العثور على الوقت
            time_elem = item.find('time') or item.find('span', class_='date')
            time_text = time_elem.text.strip() if time_elem else "قبل قليل"
            
            # تصنيف الخبر
            news_type = "عام"
            title_lower = title.lower()
            for type_name, keywords in KEYWORDS.items():
                for keyword in keywords:
                    if keyword.lower() in title_lower:
                        news_type = type_name
                        break
            
            # معرّف فريد للخبر
            import hashlib
            unique_id = hashlib.md5(f"{title}{time_text}".encode()).hexdigest()[:10]
            
            article_data = {
                'title': title,
                'link': link,
                'time': time_text,
                'category': category,
                'type': news_type,
                'unique_id': unique_id
            }
            
            articles.append(article_data)
        except Exception as e:
            continue
    
    return articles

def filter_important_news(articles):
    important = []
    for article in articles:
        if article['type'] != "عام" and article['link']:
            important.append(article)
    return important

async def send_telegram_message(bot, article):
    try:
        emoji_map = {
            'فائدة': '🏦',
            'تضخم': '📈',
            'بطالة': '👥',
            'ناتج': '📊',
            'نفط': '🛢️',
            'ذهب': '💰',
            'حرب': '⚔️',
            'عقوبات': '🚫'
        }
        
        emoji = emoji_map.get(article['type'], '📰')
        
        # تنسيق الرسالة بشكل أفضل
        message = f"""
{emoji} **{article['type'].upper()}** | {article['category']} {emoji}

{article['title']}

⏰ {article['time']}

🔗 [قراءة الخبر]({article['link']})
        """
        
        await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message,
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logging.info(f"✅ تم إرسال خبر: {article['title'][:40]}...")
        sent_articles.add(article['unique_id'])
        return True
        
    except Exception as e:
        logging.error(f"❌ خطأ في إرسال الرسالة: {e}")
        return False

# ========== الدورة الرئيسية ==========
async def news_check_loop():
    """الدورة الرئيسية للفحص التلقائي"""
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                logging.info("🔄 بدء فحص الأخبار التلقائي...")
                
                all_articles = []
                
                # جلب الأخبار من جميع المصادر
                for category, url in NEWS_URLS.items():
                    try:
                        articles = await fetch_news(session, url, category)
                        all_articles.extend(articles)
                        logging.info(f"   📰 {category}: {len(articles)} خبر")
                        await asyncio.sleep(1)  # انتظار بين الطلبات
                    except Exception as e:
                        logging.error(f"   ❌ خطأ في {category}: {e}")
                
                # تصفية الأخبار المهمة
                important_news = filter_important_news(all_articles)
                
                # إرسال الأخبار الجديدة فقط
                new_count = 0
                for article in important_news:
                    if article['unique_id'] not in sent_articles:
                        success = await send_telegram_message(bot, article)
                        if success:
                            new_count += 1
                            await asyncio.sleep(2)  # انتظار بين الإرسال
                
                # تنظيف الذاكرة
                if len(sent_articles) > 500:
                    # حفظ آخر 500 فقط
                    sent_list = list(sent_articles)
                    sent_articles.clear()
                    sent_articles.update(sent_list[-500:])
                
                if new_count > 0:
                    logging.info(f"📤 تم إرسال {new_count} خبر جديد")
                else:
                    logging.info("ℹ️ لا توجد أخبار جديدة مهمة")
                
                logging.info(f"⏳ الانتظار {CHECK_INTERVAL} ثانية للفحص التالي...")
                
            except Exception as e:
                logging.error(f"🚨 خطأ في الدورة الرئيسية: {e}")
            
            await asyncio.sleep(CHECK_INTERVAL)

def run_once_check():
    """فحص يدوي لمرة واحدة"""
    async def one_time():
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        async with aiohttp.ClientSession() as session:
            logging.info("🔍 بدء فحص يدوي...")
            all_articles = []
            for category, url in NEWS_URLS.items():
                articles = await fetch_news(session, url, category)
                all_articles.extend(articles)
            
            important = filter_important_news(all_articles)
            for article in important[:3]:  # أول 3 فقط في الفحص اليدوي
                if article['unique_id'] not in sent_articles:
                    await send_telegram_message(bot, article)
                    await asyncio.sleep(1)
    
    asyncio.run(one_time())

def start_bot():
    """بدء تشغيل البوت"""
    global bot_started
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # اختبار البوت أولاً
        test_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        test_info = loop.run_until_complete(test_bot.get_me())
        logging.info(f"🤖 البوت جاهز: @{test_info.username}")
        
        # بدء الدورة الرئيسية
        bot_started = True
        loop.run_until_complete(news_check_loop())
    except Exception as e:
        logging.error(f"🚨 فشل بدء البوت: {e}")
        bot_started = False

# ========== بدء التشغيل ==========
if __name__ == "__main__":
    # إعداد التسجيل
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('bot.log', encoding='utf-8')
        ]
    )
    
    logger = logging.getLogger(__name__)
    
    # بدء البوت في thread منفصل
    def run_flask():
        port = int(os.getenv('PORT', 10000))
        app.run(host='0.0.0.0', port=port, debug=False)
    
    # بدء Flask في thread منفصل
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    logging.info("🚀 بدء تشغيل بوت أخبار الأسواق...")
    logging.info(f"📢 القناة: {CHANNEL_USERNAME}")
    logging.info(f"⏰ فترة الفحص: {CHECK_INTERVAL} ثانية")
    
    # بدء البوت بعد تأخير قصير
    time.sleep(3)
    start_bot()
