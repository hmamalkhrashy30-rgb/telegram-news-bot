import os
import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from telegram import Bot
from flask import Flask, jsonify
import threading
import time
import random
import hashlib
from datetime import datetime
from fake_useragent import UserAgent

# ========== إعدادات ==========
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@DO_IUi')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '1800'))  # 30 دقيقة

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN غير محدد.")

# ========== Flask App ==========
app = Flask(__name__)

# ========== مصادر الأخبار البديلة ==========
NEWS_SOURCES = [
    # مصادر Investing.com (الصفحات الرئيسية)
    ("أخبار اقتصادية", "https://www.investing.com/", "economy"),
    ("أسواق المال", "https://www.investing.com/markets/", "markets"),
    ("سلع", "https://www.investing.com/commodities/", "commodities"),
    
    # مصادر بديلة
    ("رويترز اقتصاد", "https://www.reuters.com/business/", "reuters"),
    ("بلومبرج", "https://www.bloomberg.com/markets", "bloomberg"),
    ("CNN أعمال", "https://edition.cnn.com/business", "cnn"),
    
    # مصادر RSS مباشرة (أسهل وأسرع)
    ("Investing RSS", "https://www.investing.com/rss/news.rss", "rss"),
    ("Reuters RSS", "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best", "rss"),
]

# مصادر RSS مباشرة
RSS_FEEDS = [
    ("أخبار Investing", "https://www.investing.com/rss/news_285.rss"),
    ("أخبار الأسواق", "https://www.investing.com/rss/news_25.rss"),
    ("أخبار النفط", "https://www.investing.com/rss/news_3.rss"),
    ("أخبار الذهب", "https://www.investing.com/rss/news_4.rss"),
    ("أخبار العملات", "https://www.investing.com/rss/news_2.rss"),
]

KEYWORDS = {
    'فائدة': ['interest rate', 'fed', 'central bank', 'فائدة', 'بنك مركزي', 'الفيدرالي'],
    'تضخم': ['cpi', 'inflation', 'تضخم', 'أسعار', 'inflation'],
    'بطالة': ['unemployment', 'jobs', 'بطالة', 'وظائف', 'employment'],
    'ناتج': ['gdp', 'growth', 'ناتج', 'اقتصاد', 'اقتصادي'],
    'نفط': ['oil', 'crude', 'بترول', 'نفط', 'أوبك', 'النفط'],
    'ذهب': ['gold', 'ذهب', 'معدن', 'الذهب', 'bullion'],
    'حرب': ['war', 'conflict', 'حرب', 'صراع', 'نزاع'],
    'عقوبات': ['sanctions', 'عقوبات', 'عقوبة', 'embargo'],
}

# تخزين
sent_articles = set()
bot_started = False
last_check_time = None
ua = UserAgent()

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "Telegram News Bot",
        "channel": CHANNEL_USERNAME,
        "bot_started": bot_started,
        "articles_in_memory": len(sent_articles),
        "last_check": last_check_time,
        "uptime": time.strftime("%H:%M:%S", time.gmtime(time.time() - start_time))
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "time": datetime.now().isoformat()}), 200

@app.route('/check')
def check_now():
    """فحص يدوي"""
    threading.Thread(target=run_manual_check).start()
    return jsonify({"message": "بدأ الفحص اليدوي", "time": datetime.now().strftime("%H:%M:%S")})

@app.route('/test/<path:url>')
def test_url(url):
    """اختبار جلب URL معين"""
    async def test():
        async with aiohttp.ClientSession() as session:
            headers = await get_headers()
            try:
                async with session.get(f"https://{url}", headers=headers, timeout=10) as resp:
                    return jsonify({
                        "url": url,
                        "status": resp.status,
                        "headers": dict(resp.headers)
                    })
            except Exception as e:
                return jsonify({"error": str(e)}), 500
    
    return asyncio.run(test())

# ========== وظائف مساعدة ==========
async def get_headers():
    """إنشاء headers محاكية للمتصفح"""
    return {
        'User-Agent': ua.random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.google.com/',
    }

def get_proxy():
    """الحصول على proxy مجاني (اختياري)"""
    proxies = [
        None,  # بدون proxy أولاً
        'http://proxy1:8080',
        'http://proxy2:8080',
    ]
    return random.choice(proxies)

def create_id(title, source):
    """إنشاء ID فريد للخبر"""
    text = f"{title[:50]}{source}"
    return hashlib.md5(text.encode()).hexdigest()[:12]

def categorize(title):
    """تصنيف الخبر"""
    title_lower = title.lower()
    for cat, keywords in KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in title_lower:
                return cat
    return "عام"

# ========== جلب الأخبار من RSS (أفضل وأسهل) ==========
async def fetch_rss_feed(session, url, source_name):
    """جلب أخبار من RSS feed"""
    try:
        headers = await get_headers()
        async with session.get(url, headers=headers, timeout=15) as response:
            if response.status == 200:
                xml = await response.text()
                return parse_rss_feed(xml, source_name)
    except Exception as e:
        logging.error(f"❌ خطأ في RSS {source_name}: {e}")
    return []

def parse_rss_feed(xml, source_name):
    """تحليل RSS feed"""
    try:
        soup = BeautifulSoup(xml, 'xml')
        articles = []
        
        items = soup.find_all('item')[:15]  # أول 15 خبر
        
        for item in items:
            try:
                title = item.find('title').text.strip()
                link = item.find('link').text.strip()
                pub_date = item.find('pubDate')
                time_text = pub_date.text.strip() if pub_date else "قبل قليل"
                
                # وصف
                description = item.find('description')
                summary = description.text.strip()[:200] if description else ""
                
                news_type = categorize(title)
                
                article_data = {
                    'id': create_id(title, source_name),
                    'title': title,
                    'link': link,
                    'time': time_text,
                    'summary': summary,
                    'type': news_type,
                    'source': source_name,
                    'timestamp': time.time()
                }
                
                articles.append(article_data)
                
            except Exception:
                continue
        
        logging.info(f"📡 RSS {source_name}: {len(articles)} خبر")
        return articles
        
    except Exception as e:
        logging.error(f"❌ خطأ في تحليل RSS: {e}")
        return []

# ========== جلب من Investing.com (محاولة ذكية) ==========
async def fetch_investing_smart(session, url, category):
    """جلب أخبار بذكاء من Investing.com"""
    try:
        headers = await get_headers()
        
        # محاولة الصفحة الرئيسية أولاً
        async with session.get(url, headers=headers, timeout=20) as response:
            if response.status != 200:
                logging.warning(f"⚠️ {category}: حالة {response.status}")
                return []
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            articles = []
            
            # البحث عن مقالات بطرق مختلفة
            patterns = [
                # أنماط Investing.com الشائعة
                {'selector': 'article[data-test="article-item"]', 'title': 'a[data-test="article-title"]'},
                {'selector': 'div.articleItem', 'title': 'a.title'},
                {'selector': 'div.largeTitle', 'title': 'a'},
                {'selector': 'div.mediumTitle', 'title': 'a'},
                {'selector': 'div.textDiv', 'title': 'a'},
                {'selector': '[class*="article"]', 'title': '[class*="title"]'},
            ]
            
            for pattern in patterns:
                items = soup.select(pattern['selector'])[:10]
                if items:
                    for item in items:
                        try:
                            title_elem = item.select_one(pattern['title'])
                            if not title_elem:
                                continue
                            
                            title = title_elem.text.strip()
                            if len(title) < 10:
                                continue
                            
                            link = title_elem.get('href', '')
                            if link and not link.startswith('http'):
                                link = f"https://www.investing.com{link}"
                            
                            time_elem = item.find('time') or item.find('span', class_='date')
                            time_text = time_elem.text.strip() if time_elem else "قبل قليل"
                            
                            news_type = categorize(title)
                            
                            article_data = {
                                'id': create_id(title, category),
                                'title': title,
                                'link': link,
                                'time': time_text,
                                'type': news_type,
                                'source': category,
                                'timestamp': time.time()
                            }
                            
                            articles.append(article_data)
                            
                        except Exception:
                            continue
                    
                    if articles:
                        break
            
            logging.info(f"📡 {category}: وجد {len(articles)} خبر")
            return articles
            
    except Exception as e:
        logging.error(f"❌ خطأ في {category}: {e}")
        return []

# ========== إرسال إلى تليجرام ==========
async def send_to_channel(bot, article):
    """إرسال خبر إلى القناة"""
    try:
        emoji_map = {
            'فائدة': '🏦', 'تضخم': '📈', 'بطالة': '👥',
            'ناتج': '📊', 'نفط': '🛢️', 'ذهب': '💰',
            'حرب': '⚔️', 'عقوبات': '🚫', 'عام': '📰'
        }
        
        emoji = emoji_map.get(article['type'], '📰')
        
        # تنسيق الرسالة
        message = f"""
{emoji} **{article['type'].upper()}** | {article['source']}

{article['title']}

⏰ {article['time']}

🔗 [قراءة الخبر]({article['link']})
        """
        
        await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message[:4000],
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logging.info(f"✅ تم إرسال: {article['title'][:60]}...")
        sent_articles.add(article['id'])
        return True
        
    except Exception as e:
        logging.error(f"❌ فشل إرسال: {str(e)[:100]}")
        return False

# ========== الدورة الرئيسية ==========
async def news_bot_loop():
    """الدورة الرئيسية للبوت"""
    global bot_started, last_check_time
    
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot_info = await bot.get_me()
        logging.info(f"🤖 البوت جاهز: @{bot_info.username}")
        
        # إرسال رسالة بدء التشغيل
        try:
            await bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text="🚀 بوت الأخبار المالية يعمل الآن!\nسيتم إرسال آخر الأخبار الاقتصادية تلقائياً."
            )
        except:
            pass
        
        bot_started = True
        
    except Exception as e:
        logging.error(f"❌ فشل بدء البوت: {e}")
        return
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                last_check_time = datetime.now().strftime("%H:%M:%S")
                logging.info("=" * 60)
                logging.info(f"🔄 بدء فحص جديد: {last_check_time}")
                
                all_articles = []
                
                # 1. أولاً: جلب من RSS (الأسهل والأكثر موثوقية)
                logging.info("📡 مرحلة 1: جلب من RSS feeds...")
                for source_name, rss_url in RSS_FEEDS:
                    articles = await fetch_rss_feed(session, rss_url, source_name)
                    all_articles.extend(articles)
                    await asyncio.sleep(1)
                
                # 2. ثانياً: جلب من Investing.com (إذا كان RSS قليلاً)
                if len(all_articles) < 5:
                    logging.info("📡 مرحلة 2: جلب من Investing.com...")
                    for category, url, _ in NEWS_SOURCES[:3]:  # أول 3 مصادر فقط
                        articles = await fetch_investing_smart(session, url, category)
                        all_articles.extend(articles)
                        await asyncio.sleep(2)
                
                # 3. تصفية وترتيب
                important_articles = [a for a in all_articles if a['type'] != 'عام']
                important_articles.sort(key=lambda x: x['timestamp'], reverse=True)
                
                # 4. إرسال الجديدة
                sent_count = 0
                for article in important_articles[:8]:  # أول 8 مهمة فقط
                    if article['id'] not in sent_articles:
                        success = await send_to_channel(bot, article)
                        if success:
                            sent_count += 1
                            await asyncio.sleep(2)
                
                # 5. الإحصائيات
                logging.info("=" * 60)
                logging.info(f"📊 النتائج:")
                logging.info(f"   📝 إجمالي الأخبار: {len(all_articles)}")
                logging.info(f"   ⭐ الأخبار المهمة: {len(important_articles)}")
                logging.info(f"   📤 المرسلة حديثاً: {sent_count}")
                logging.info(f"   💾 المخزنة: {len(sent_articles)}")
                
                if len(all_articles) == 0:
                    logging.warning("⚠️ لم يتم العثور على أخبار! اختبار الاتصال...")
                    test_resp = await session.get('https://www.google.com', headers=await get_headers())
                    logging.info(f"🌐 اختبار الاتصال: {test_resp.status}")
                
                logging.info("=" * 60)
                
            except Exception as e:
                logging.error(f"🚨 خطأ في الدورة: {e}")
            
            logging.info(f"⏳ الانتظار {CHECK_INTERVAL//60} دقائق للفحص التالي...")
            await asyncio.sleep(CHECK_INTERVAL)

def run_manual_check():
    """فحص يدوي"""
    async def check():
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            async with aiohttp.ClientSession() as session:
                logging.info("🔍 فحص يدوي سريع...")
                
                # اختبار RSS مباشرة
                articles = []
                for source_name, rss_url in RSS_FEEDS[:2]:  # أول مصدرين فقط
                    feed_articles = await fetch_rss_feed(session, rss_url, source_name)
                    articles.extend(feed_articles)
                
                if articles:
                    logging.info(f"✅ الفحص اليدوي: {len(articles)} خبر")
                    for article in articles[:3]:  # أول 3
                        await send_to_channel(bot, article)
                        await asyncio.sleep(1)
                else:
                    logging.warning("⚠️ الفحص اليدوي: 0 خبر")
                    
        except Exception as e:
            logging.error(f"❌ خطأ في الفحص اليدوي: {e}")
    
    asyncio.run(check())

def start_bot():
    """بدء البوت في الخلفية"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(news_bot_loop())

def run_flask():
    """تشغيل Flask"""
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ========== التشغيل الرئيسي ==========
if __name__ == "__main__":
    # إعداد التسجيل
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    start_time = time.time()
    
    logging.info("=" * 70)
    logging.info("🚀 بدء تشغيل بوت الأخبار المالية المتقدم")
    logging.info(f"📢 القناة: {CHANNEL_USERNAME}")
    logging.info(f"⏰ فترة الفحص: {CHECK_INTERVAL} ثانية ({CHECK_INTERVAL//60} دقيقة)")
    logging.info(f"📡 مصادر RSS: {len(RSS_FEEDS)}")
    logging.info("=" * 70)
    
    # بدء Flask في thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # تأخير ثم بدء البوت
    time.sleep(5)
    
    # بدء البوت في thread منفصل
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    
    # إبقاء البرنامج يعمل
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logging.info("👋 إيقاف البوت...")
