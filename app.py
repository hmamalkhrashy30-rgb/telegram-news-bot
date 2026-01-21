import os
import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError
from flask import Flask, jsonify, request
import threading
import time
import random
import hashlib
from datetime import datetime

# ========== إعدادات ==========
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@MarketNewsArabia')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '1200'))  # 20 دقيقة

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN غير محدد.")

# ========== Flask App ==========
app = Flask(__name__)

# ========== مصادر الأخبار ==========
INVESTING_URLS = [
    ("أخبار اقتصادية", "https://www.investing.com/news/economy-news"),
    ("الفيدرالي", "https://www.investing.com/central-banks/fed"),
    ("تضخم", "https://www.investing.com/news/inflation-news"),
    ("وظائف", "https://www.investing.com/news/employment-news"),
    ("نفط", "https://www.investing.com/commodities/crude-oil-news")
]

# مصادر احتياطية
BACKUP_URLS = [
    ("أخبار مالية", "https://www.investing.com/news/latest-news"),
    ("أخبار عامة", "https://www.investing.com/news/most-popular-news")
]

KEYWORDS = {
    'فائدة': ['interest rate', 'fed', 'central bank', 'فائدة', 'بنك مركزي'],
    'تضخم': ['cpi', 'inflation', 'تضخم', 'أسعار'],
    'بطالة': ['unemployment', 'jobs', 'بطالة', 'وظائف'],
    'ناتج': ['gdp', 'growth', 'ناتج', 'اقتصاد'],
    'نفط': ['oil', 'crude', 'بترول', 'نفط'],
    'ذهب': ['gold', 'ذهب', 'معدن'],
    'حرب': ['war', 'conflict', 'حرب', 'صراع'],
    'عقوبات': ['sanctions', 'عقوبات']
}

# تخزين
sent_articles = set()
bot_started = False
last_check_time = None

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "Telegram News Bot",
        "channel": CHANNEL_USERNAME,
        "bot_started": bot_started,
        "articles_in_memory": len(sent_articles),
        "last_check": last_check_time,
        "endpoints": {
            "health": "/health",
            "manual_check": "/check-now",
            "stats": "/stats"
        }
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/check-now')
def manual_check():
    """فحص يدوي"""
    threading.Thread(target=run_one_time_check).start()
    return jsonify({
        "message": "بدء الفحص اليدوي...",
        "time": datetime.now().strftime("%H:%M:%S")
    })

@app.route('/stats')
def stats():
    return jsonify({
        "sent_articles": len(sent_articles),
        "bot_started": bot_started,
        "check_interval": CHECK_INTERVAL,
        "channel": CHANNEL_USERNAME
    })

# ========== وظائف مساعدة ==========
def get_user_agent():
    agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
    ]
    return random.choice(agents)

def create_article_id(title, time_text):
    """إنشاء معرّف فريد للخبر"""
    text = f"{title[:30]}{time_text}"
    return hashlib.md5(text.encode()).hexdigest()[:10]

def categorize_article(title):
    """تصنيف الخبر"""
    title_lower = title.lower()
    for cat, keywords in KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in title_lower:
                return cat
    return "عام"

# ========== وظائف جلب الأخبار ==========
async def fetch_investing_page(session, url, category):
    """جلب صفحة من Investing.com"""
    headers = {
        'User-Agent': get_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive'
    }
    
    try:
        logging.info(f"📡 جلب: {category}")
        async with session.get(url, headers=headers, timeout=15) as response:
            if response.status == 200:
                html = await response.text()
                return await parse_investing_page(html, category)
            else:
                logging.warning(f"⚠️ حالة {response.status} لـ {category}")
                return []
    except Exception as e:
        logging.error(f"❌ خطأ في {category}: {str(e)[:100]}")
        return []

async def parse_investing_page(html, category):
    """تحليل صفحة Investing.com"""
    soup = BeautifulSoup(html, 'html.parser')
    articles = []
    
    # محاولات مختلفة للعثور على الأخبار
    selectors_to_try = [
        'article[class*="article"]',
        'div[class*="article"]',
        'div.largeTitle',
        'div.mediumTitle',
        'div.textDiv',
        'a.title'
    ]
    
    found_items = []
    for selector in selectors_to_try:
        found_items = soup.select(selector)
        if found_items:
            logging.info(f"✅ وجد {len(found_items)} عنصر بـ {selector}")
            break
    
    # إذا لم نجد، نجرب البحث عن أي مقالات
    if not found_items:
        all_articles = soup.find_all(['article', 'div'], 
                                    class_=lambda x: x and any(word in str(x) for word in ['article', 'news', 'item']))
        found_items = all_articles[:15]
    
    for item in found_items[:10]:  # أول 10 فقط
        try:
            # البحث عن العنوان
            title_elem = None
            for tag in ['a', 'h3', 'h2', 'div', 'span']:
                title_elem = item.find(tag, class_=lambda x: x and 'title' in str(x).lower())
                if title_elem:
                    break
            
            if not title_elem:
                # محاولة أخيرة: أي رابط
                title_elem = item.find('a')
            
            if not title_elem:
                continue
            
            title = title_elem.get_text(strip=True)
            if len(title) < 5:
                continue
            
            # الرابط
            link = title_elem.get('href', '')
            if link and not link.startswith('http'):
                if link.startswith('//'):
                    link = 'https:' + link
                else:
                    link = 'https://www.investing.com' + link
            
            # الوقت
            time_elem = item.find('time') or item.find('span', class_=lambda x: x and any(word in str(x) for word in ['time', 'date']))
            time_text = time_elem.get_text(strip=True) if time_elem else "قبل قليل"
            
            # التصنيف
            news_type = categorize_article(title)
            
            # معرّف
            article_id = create_article_id(title, time_text)
            
            article_data = {
                'id': article_id,
                'title': title,
                'link': link if link.startswith('http') else f"https://www.investing.com{link}",
                'time': time_text,
                'type': news_type,
                'category': category,
                'timestamp': time.time()
            }
            
            articles.append(article_data)
            
        except Exception as e:
            continue
    
    logging.info(f"📊 {category}: تم تحليل {len(articles)} خبر")
    return articles

async def send_telegram_article(bot, article):
    """إرسال خبر إلى تليجرام"""
    try:
        emojis = {
            'فائدة': '🏦', 'تضخم': '📈', 'بطالة': '👥',
            'ناتج': '📊', 'نفط': '🛢️', 'ذهب': '💰',
            'حرب': '⚔️', 'عقوبات': '🚫', 'عام': '📰'
        }
        
        emoji = emojis.get(article['type'], '📰')
        
        message = f"""
{emoji} **{article['type'].upper()}** | {article['category']}

{article['title']}

⏰ {article['time']}

🔗 [اقرأ الخبر]({article['link']})
        """
        
        await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message,
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logging.info(f"✅ تم إرسال: {article['title'][:50]}...")
        return True
        
    except Exception as e:
        logging.error(f"❌ فشل إرسال: {str(e)[:100]}")
        return False

# ========== الدورة الرئيسية - معدلة ==========
async def main_news_loop():
    """الدورة الرئيسية المعدلة"""
    global bot_started, last_check_time
    
    # اختبار البوت أولاً
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot_info = await bot.get_me()  # هنا يجب أن يكون await
        logging.info(f"🤖 البوت جاهز: @{bot_info.username}")
        bot_started = True
    except Exception as e:
        logging.error(f"❌ فشل اختبار البوت: {e}")
        bot_started = False
        return
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                last_check_time = datetime.now().strftime("%H:%M:%S")
                logging.info("=" * 50)
                logging.info(f"🔄 بدء فحص: {last_check_time}")
                
                # جمع الأخبار من جميع المصادر
                all_articles = []
                
                # مصادر Investing.com
                for category, url in INVESTING_URLS:
                    articles = await fetch_investing_page(session, url, category)
                    all_articles.extend(articles)
                    await asyncio.sleep(2)
                
                # مصادر احتياطية إذا كان العدد قليلاً
                if len(all_articles) < 3:
                    logging.info("⚠️ القليل من الأخبار، جاري استخدام المصادر الاحتياطية...")
                    for category, url in BACKUP_URLS:
                        articles = await fetch_investing_page(session, url, category)
                        all_articles.extend(articles)
                        await asyncio.sleep(2)
                
                # تصفية المهمة
                important_articles = [a for a in all_articles if a['type'] != 'عام']
                
                # إرسال الجديدة
                sent_count = 0
                for article in important_articles[:5]:  # أول 5 مهمة فقط
                    if article['id'] not in sent_articles:
                        success = await send_telegram_article(bot, article)
                        if success:
                            sent_articles.add(article['id'])
                            sent_count += 1
                            await asyncio.sleep(3)
                
                # تنظيف الذاكرة
                if len(sent_articles) > 200:
                    sent_articles.clear()
                
                # إحصائيات
                logging.info("=" * 50)
                logging.info(f"📊 الإحصائيات:")
                logging.info(f"   📝 إجمالي الأخبار: {len(all_articles)}")
                logging.info(f"   ⭐ المهمة: {len(important_articles)}")
                logging.info(f"   📤 الجديدة المرسلة: {sent_count}")
                logging.info("=" * 50)
                
                if len(all_articles) == 0:
                    logging.warning("⚠️ لم يتم العثور على أي أخبار!")
                    # اختبار بسيط
                    test_response = await session.get('https://www.investing.com', 
                                                      headers={'User-Agent': get_user_agent()})
                    logging.info(f"🔗 اختبار الاتصال: {test_response.status}")
                
            except Exception as e:
                logging.error(f"🚨 خطأ في الدورة: {e}")
            
            logging.info(f"⏳ الانتظار {CHECK_INTERVAL} ثانية...")
            await asyncio.sleep(CHECK_INTERVAL)

def run_one_time_check():
    """فحص يدوي لمرة واحدة"""
    async def single_check():
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            async with aiohttp.ClientSession() as session:
                logging.info("🔍 بدء فحص يدوي...")
                # صفحة اختبار واحدة
                articles = await fetch_investing_page(session, 
                                                     "https://www.investing.com/news/latest-news", 
                                                     "اختبار")
                if articles:
                    logging.info(f"✅ الفحص اليدوي: وجد {len(articles)} خبر")
                    for article in articles[:2]:
                        await send_telegram_article(bot, article)
                        await asyncio.sleep(2)
                else:
                    logging.warning("⚠️ الفحص اليدوي: 0 خبر")
        except Exception as e:
            logging.error(f"❌ خطأ في الفحص اليدوي: {e}")
    
    # تشغيل الـ async function
    asyncio.run(single_check())

def start_background_bot():
    """بدء البوت في الخلفية"""
    global bot_started
    
    try:
        # إنشاء event loop جديد
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # بدء الدورة الرئيسية
        loop.run_until_complete(main_news_loop())
        
    except Exception as e:
        logging.error(f"❌ فشل بدء البوت: {e}")
        bot_started = False

# ========== تشغيل Flask ==========
def run_flask_app():
    """تشغيل تطبيق Flask"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ========== نقطة الدخول الرئيسية ==========
if __name__ == "__main__":
    # إعداد التسجيل
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    logging.info("=" * 60)
    logging.info("🚀 بدء تشغيل بوت Investing.com للأخبار")
    logging.info(f"📢 القناة: {CHANNEL_USERNAME}")
    logging.info(f"⏰ فترة الفحص: {CHECK_INTERVAL} ثانية")
    logging.info(f"📡 مصادر Investing.com: {len(INVESTING_URLS)}")
    logging.info(f"📡 مصادر احتياطية: {len(BACKUP_URLS)}")
    logging.info("=" * 60)
    
    # بدء Flask في thread منفصل
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    
    # تأخير ثم بدء البوت
    time.sleep(3)
    
    # بدء البوت في thread منفصل
    bot_thread = threading.Thread(target=start_background_bot, daemon=True)
    bot_thread.start()
    
    # إبقاء البرنامج يعمل
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logging.info("👋 إيقاف البوت...")
