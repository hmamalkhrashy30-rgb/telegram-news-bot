import os
import logging
import asyncio
import aiohttp
import aiohttp.client_exceptions
from bs4 import BeautifulSoup
from telegram import Bot, error
from flask import Flask, jsonify
import threading
import time
import hashlib
from datetime import datetime
import xml.etree.ElementTree as ET
import re

# ========== إعدادات ==========
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
# مهم: يجب أن يكون معرف القناة (يبدأ بـ @) وليس رابط
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@DO_IUi')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '600'))  # 10 دقائق

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN غير محدد.")

# تنظيف معرف القناة إذا كان رابطاً
if CHANNEL_USERNAME.startswith('http'):
    # استخراج المعرف من الرابط
    match = re.search(r't\.me/(\w+)', CHANNEL_USERNAME)
    if match:
        CHANNEL_USERNAME = f"@{match.group(1)}"
elif not CHANNEL_USERNAME.startswith('@'):
    CHANNEL_USERNAME = f"@{CHANNEL_USERNAME}"

logging.info(f"📢 القناة المضبوطة: {CHANNEL_USERNAME}")

# ========== Flask App ==========
app = Flask(__name__)

# ========== مصادر RSS مباشرة (بدون Brotli issues) ==========
RSS_FEEDS = [
    ("اقتصاد", "https://www.investing.com/rss/news_285.rss"),
    ("أسواق", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC,^DJI,^IXIC&region=US&lang=en-US"),
    ("نفط", "https://www.nasdaq.com/feed/rssoutbound?symbol=CL%3DF"),
    ("ذهب", "https://www.nasdaq.com/feed/rssoutbound?symbol=GC%3DF"),
    ("عملات", "https://www.ecb.europa.eu/rss/fxref-usd.html"),
    ("فيدرالي", "https://www.federalreserve.gov/feeds/press_all.xml"),
]

KEYWORDS = {
    'فائدة': ['interest rate', 'fed', 'central bank', 'فائدة', 'rates', 'monetary'],
    'تضخم': ['cpi', 'inflation', 'تضخم', 'أسعار', 'prices', 'consumer'],
    'بطالة': ['unemployment', 'jobs', 'بطالة', 'وظائف', 'employment', 'hiring'],
    'ناتج': ['gdp', 'growth', 'ناتج', 'اقتصاد', 'economy', 'economic'],
    'نفط': ['oil', 'crude', 'بترول', 'نفط', 'أوبك', 'opec', 'energy'],
    'ذهب': ['gold', 'ذهب', 'معدن', 'precious', 'bullion', 'metal'],
    'حرب': ['war', 'conflict', 'حرب', 'صراع', 'tension', 'military'],
    'عقوبات': ['sanctions', 'عقوبات', 'embargo', 'ban', 'restrictions'],
}

# تخزين
sent_articles = set()
bot_started = False

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "Telegram News Bot",
        "channel": CHANNEL_USERNAME,
        "bot_started": bot_started,
        "articles_sent": len(sent_articles),
        "endpoints": {
            "health": "/health",
            "manual_check": "/check",
            "test_channel": "/test-channel"
        }
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/check')
def manual_check():
    """فحص يدوي سريع"""
    threading.Thread(target=run_quick_check).start()
    return jsonify({
        "message": "بدأ الفحص اليدوي السريع",
        "time": datetime.now().strftime("%H:%M:%S")
    })

@app.route('/test-channel')
def test_channel():
    """اختبار إرسال رسالة إلى القناة"""
    async def test():
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text="✅ اختبار: البوت يعمل بنجاح!\n" +
                     "سيبدأ إرسال الأخبار الاقتصادية قريباً."
            )
            return jsonify({"success": True, "channel": CHANNEL_USERNAME})
        except error.BadRequest as e:
            return jsonify({"error": str(e), "channel": CHANNEL_USERNAME}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    return asyncio.run(test())

# ========== وظائف RSS محسنة ==========
async def fetch_rss_safe(session, url, source_name):
    """جلب RSS بأمان مع headers مناسبة"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; RSSBot/1.0)',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Encoding': 'gzip, deflate',  # لا نطلب brotli
    }
    
    try:
        async with session.get(url, headers=headers, timeout=15) as response:
            if response.status == 200:
                content_type = response.headers.get('Content-Type', '')
                
                # معالجة محتوى XML
                if 'xml' in content_type or 'rss' in content_type or url.endswith('.xml') or url.endswith('.rss'):
                    text = await response.text()
                    return parse_rss_xml(text, source_name)
                else:
                    # محاولة كـ HTML
                    text = await response.text()
                    return parse_html_for_news(text, source_name)
                    
    except aiohttp.client_exceptions.ClientError as e:
        logging.error(f"❌ خطأ شبكة في {source_name}: {e}")
    except asyncio.TimeoutError:
        logging.error(f"⏰ timeout في {source_name}")
    except Exception as e:
        logging.error(f"❌ خطأ في {source_name}: {e}")
    
    return []

def parse_rss_xml(xml_text, source_name):
    """تحليل XML لـ RSS"""
    articles = []
    
    try:
        # تنظيف النص أولاً
        xml_text = re.sub(r'encoding="[^"]+"', 'encoding="utf-8"', xml_text)
        xml_text = re.sub(r'&(?!(?:amp|lt|gt|quot|apos);)', '&amp;', xml_text)
        
        root = ET.fromstring(xml_text)
        
        # البحث عن items في RSS
        items = []
        for elem in root.iter():
            if 'item' in elem.tag:
                items.append(elem)
        
        if not items:
            # محاولة بديلة
            items = root.findall('.//item') or root.findall('.//entry')
        
        for item in items[:12]:  # أول 12 فقط
            try:
                # استخراج العنوان
                title_elem = item.find('title') or item.find('{http://www.w3.org/2005/Atom}title')
                if title_elem is None:
                    continue
                    
                title = title_elem.text.strip() if title_elem.text else ""
                if not title or len(title) < 10:
                    continue
                
                # استخراج الرابط
                link_elem = item.find('link') or item.find('{http://www.w3.org/2005/Atom}link')
                link = ""
                if link_elem is not None:
                    if link_elem.text:
                        link = link_elem.text.strip()
                    elif 'href' in link_elem.attrib:
                        link = link_elem.attrib['href']
                
                # استخراج الوقت
                date_elem = item.find('pubDate') or item.find('published') or item.find('date')
                time_text = date_elem.text.strip() if date_elem is not None and date_elem.text else "قبل قليل"
                
                # استخراج الملخص
                desc_elem = item.find('description') or item.find('summary') or item.find('content')
                summary = desc_elem.text.strip()[:150] if desc_elem is not None and desc_elem.text else ""
                
                # تصنيف
                news_type = categorize_news(title)
                
                # معرّف فريد
                article_id = hashlib.md5(f"{title[:40]}{source_name}".encode()).hexdigest()[:10]
                
                articles.append({
                    'id': article_id,
                    'title': title,
                    'link': link,
                    'time': time_text,
                    'summary': summary,
                    'type': news_type,
                    'source': source_name,
                    'timestamp': time.time()
                })
                
            except Exception as e:
                continue
        
        logging.info(f"✅ RSS {source_name}: {len(articles)} خبر")
        
    except ET.ParseError as e:
        logging.error(f"❌ خطأ في تحليل XML لـ {source_name}: {e}")
        # محاولة بـ BeautifulSoup كبديل
        try:
            soup = BeautifulSoup(xml_text, 'html.parser')
            items = soup.find_all(['item', 'entry'])[:10]
            for item in items:
                try:
                    title = item.find('title')
                    if title:
                        title = title.text.strip()
                        if len(title) > 10:
                            articles.append({
                                'id': hashlib.md5(title.encode()).hexdigest()[:10],
                                'title': title,
                                'link': "",
                                'time': "قبل قليل",
                                'summary': "",
                                'type': categorize_news(title),
                                'source': source_name,
                                'timestamp': time.time()
                            })
                except:
                    continue
            logging.info(f"✅ RSS (بديل) {source_name}: {len(articles)} خبر")
        except:
            pass
    
    return articles

def parse_html_for_news(html, source_name):
    """تحليل HTML لأخبار (كبديل)"""
    soup = BeautifulSoup(html, 'html.parser')
    articles = []
    
    # البحث عن عناوين
    headlines = []
    for tag in ['h1', 'h2', 'h3', 'h4']:
        headlines.extend(soup.find_all(tag))
    
    for headline in headlines[:15]:
        title = headline.get_text(strip=True)
        if len(title) > 20 and len(title) < 200:
            news_type = categorize_news(title)
            if news_type != "عام":  # فقط المهمة
                articles.append({
                    'id': hashlib.md5(title.encode()).hexdigest()[:10],
                    'title': title,
                    'link': "",
                    'time': "حديث",
                    'summary': "",
                    'type': news_type,
                    'source': source_name,
                    'timestamp': time.time()
                })
    
    return articles

def categorize_news(title):
    """تصنيف الخبر"""
    title_lower = title.lower()
    for category, keywords in KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in title_lower:
                return category
    return "عام"

# ========== إرسال إلى تليجرام ==========
async def send_news_to_channel(bot, article):
    """إرسال خبر إلى القناة"""
    try:
        # إيموجيات حسب النوع
        emoji_map = {
            'فائدة': '🏦', 'تضخم': '📈', 'بطالة': '👥',
            'ناتج': '📊', 'نفط': '🛢️', 'ذهب': '💰',
            'حرب': '⚔️', 'عقوبات': '🚫'
        }
        
        emoji = emoji_map.get(article['type'], '📰')
        
        # بناء الرسالة
        message_lines = []
        message_lines.append(f"{emoji} **{article['type'].upper()}** | {article['source']}")
        message_lines.append("")
        message_lines.append(f"{article['title']}")
        message_lines.append("")
        
        if article['summary']:
            message_lines.append(f"{article['summary']}")
            message_lines.append("")
        
        message_lines.append(f"⏰ {article['time']}")
        
        if article['link']:
            message_lines.append(f"🔗 [اقرأ المزيد]({article['link']})")
        
        message = "\n".join(message_lines)
        
        # إرسال
        await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message[:4000],
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logging.info(f"✅ تم إرسال: {article['title'][:50]}...")
        sent_articles.add(article['id'])
        return True
        
    except error.BadRequest as e:
        if "Chat not found" in str(e):
            logging.error(f"❌ القناة غير موجودة: {CHANNEL_USERNAME}")
            logging.error("⚠️ تأكد من:")
            logging.error("   1. القناة موجودة")
            logging.error("   2. البوت مسؤول في القناة")
            logging.error("   3. المعرف صحيح ويبدأ بـ @")
        else:
            logging.error(f"❌ خطأ في الإرسال: {e}")
        return False
        
    except Exception as e:
        logging.error(f"❌ خطأ غير متوقع: {e}")
        return False

# ========== الدورة الرئيسية ==========
async def main_news_loop():
    """الدورة الرئيسية"""
    global bot_started
    
    # اختبار البوت والقناة أولاً
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot_info = await bot.get_me()
        logging.info(f"🤖 البوت: @{bot_info.username}")
        
        # اختبار إرسال رسالة
        await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text="📢 بوت الأخبار المالية يعمل الآن!\nجاري تجميع آخر الأخبار..."
        )
        logging.info(f"✅ اختبار الإرسال ناجح إلى: {CHANNEL_USERNAME}")
        bot_started = True
        
    except error.BadRequest as e:
        logging.error(f"❌ فشل اختبار القناة: {e}")
        logging.error("⚠️ حل المشكلة:")
        logging.error("   1. تأكد من صحة معرف القناة")
        logging.error("   2. تأكد أن البوت مسؤول في القناة")
        logging.error("   3. المعرف يجب أن يكون مثل: @MarketNewsArabia")
        return
    except Exception as e:
        logging.error(f"❌ فشل بدء البوت: {e}")
        return
    
    # الدورة الرئيسية
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                logging.info("=" * 60)
                logging.info(f"🔄 بدء فحص: {datetime.now().strftime('%H:%M:%S')}")
                
                all_articles = []
                
                # جلب من مصادر RSS
                tasks = []
                for source_name, url in RSS_FEEDS:
                    tasks.append(fetch_rss_safe(session, url, source_name))
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, list):
                        all_articles.extend(result)
                
                # تصفية وترتيب
                important_articles = []
                general_articles = []
                
                for article in all_articles:
                    if article['type'] != "عام":
                        important_articles.append(article)
                    else:
                        general_articles.append(article)
                
                # ترتيب حسب الأهمية والوقت
                important_articles.sort(key=lambda x: x['timestamp'], reverse=True)
                
                # إرسال المهمة أولاً
                sent_count = 0
                for article in important_articles[:5]:  # أول 5 مهمة
                    if article['id'] not in sent_articles:
                        success = await send_news_to_channel(bot, article)
                        if success:
                            sent_count += 1
                            await asyncio.sleep(2)  # انتظار بين الإرسال
                
                # إرسال عامة إذا لم يكن هناك مهمة
                if sent_count == 0 and general_articles:
                    for article in general_articles[:3]:  # أول 3 عامة
                        if article['id'] not in sent_articles:
                            success = await send_news_to_channel(bot, article)
                            if success:
                                sent_count += 1
                                await asyncio.sleep(2)
                
                # تنظيف الذاكرة
                if len(sent_articles) > 100:
                    sent_articles.clear()
                
                # إحصائيات
                logging.info("=" * 60)
                logging.info(f"📊 النتائج:")
                logging.info(f"   📝 إجمالي الأخبار: {len(all_articles)}")
                logging.info(f"   ⭐ المهمة: {len(important_articles)}")
                logging.info(f"   📰 العامة: {len(general_articles)}")
                logging.info(f"   📤 المرسلة: {sent_count}")
                logging.info("=" * 60)
                
            except Exception as e:
                logging.error(f"🚨 خطأ في الدورة: {e}")
            
            logging.info(f"⏳ الانتظار {CHECK_INTERVAL//60} دقيقة للفحص التالي...")
            await asyncio.sleep(CHECK_INTERVAL)

def run_quick_check():
    """فحص يدوي سريع"""
    async def quick():
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            async with aiohttp.ClientSession() as session:
                logging.info("🔍 فحص يدوي سريع...")
                
                # اختبار مصدر واحد
                articles = await fetch_rss_safe(session, RSS_FEEDS[0][1], RSS_FEEDS[0][0])
                
                if articles:
                    logging.info(f"✅ الفحص: {len(articles)} خبر")
                    for article in articles[:2]:
                        await send_news_to_channel(bot, article)
                        await asyncio.sleep(1)
                else:
                    logging.warning("⚠️ لا توجد أخبار")
                    
        except Exception as e:
            logging.error(f"❌ خطأ في الفحص: {e}")
    
    asyncio.run(quick())

def start_bot_background():
    """بدء البوت في الخلفية"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main_news_loop())

def run_flask_app():
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
    
    logging.info("=" * 70)
    logging.info("🚀 بدء تشغيل بوت الأخبار المالية")
    logging.info(f"📢 القناة: {CHANNEL_USERNAME}")
    logging.info(f"⏰ فترة الفحص: {CHECK_INTERVAL} ثانية ({CHECK_INTERVAL//60} دقيقة)")
    logging.info(f"📡 مصادر RSS: {len(RSS_FEEDS)}")
    logging.info("=" * 70)
    
    # بدء Flask
    flask_thread = threading.Thread(target=run_flask_app, daemon=True)
    flask_thread.start()
    
    # تأخير ثم بدء البوت
    time.sleep(3)
    
    # بدء البوت
    bot_thread = threading.Thread(target=start_bot_background, daemon=True)
    bot_thread.start()
    
    # إبقاء البرنامج يعمل
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logging.info("👋 إيقاف البوت...")
