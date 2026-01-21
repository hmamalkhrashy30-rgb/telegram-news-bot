import os
import logging
import time
import hashlib
import feedparser
import requests
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime
import random

# ========== الإعدادات ==========
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@MarketNewsArabia')
CHECK_INTERVAL = 1200  # 20 دقيقة (أقل طلبات ممكنة)

# مصادر Investing.com عبر RSS (تعمل بشكل أفضل)
INVESTING_RSS_FEEDS = [
    # أخبار Investing.com عبر RSS
    {
        'name': 'Investing.com - Economic Indicators',
        'url': 'https://www.investing.com/rss/news_25.rss',
        'category': 'اقتصادية'
    },
    {
        'name': 'Investing.com - Fed & Central Banks',
        'url': 'https://www.investing.com/rss/news_302.rss',
        'category': 'فيدرالي'
    },
    {
        'name': 'Investing.com - Commodities',
        'url': 'https://www.investing.com/rss/news_19.rss',
        'category': 'سلع'
    },
    {
        'name': 'Investing.com - Forex',
        'url': 'https://www.investing.com/rss/news_2.rss',
        'category': 'عملات'
    },
    {
        'name': 'Investing.com - Stock Markets',
        'url': 'https://www.investing.com/rss/news_1.rss',
        'category': 'أسواق'
    }
]

# مصادر بديلة إذا فشل Investing.com
BACKUP_RSS_FEEDS = [
    {
        'name': 'Reuters Business News',
        'url': 'http://feeds.reuters.com/reuters/businessNews',
        'category': 'اقتصادية'
    },
    {
        'name': 'Bloomberg Markets',
        'url': 'https://www.bloomberg.com/feeds/podcasts/etf-report.rss',
        'category': 'أسواق'
    }
]

# كلمات مفتاحية للتصفية (العربية والإنجليزية)
KEYWORDS = {
    'فائدة': ['interest rate', 'fed', 'federal reserve', 'central bank', 'فائدة', 'بنك مركزي'],
    'تضخم': ['inflation', 'cpi', 'consumer price', 'prices', 'تضخم', 'أسعار'],
    'بطالة': ['unemployment', 'jobs', 'employment', 'nfp', 'بطالة', 'وظائف'],
    'ناتج': ['gdp', 'economic growth', 'economy', 'growth', 'ناتج', 'اقتصاد'],
    'نفط': ['oil', 'crude', 'petroleum', 'opec', 'brent', 'نفط', 'بترول'],
    'ذهب': ['gold', 'bullion', 'precious metal', 'ذهب', 'معدن'],
    'حرب': ['war', 'conflict', 'tension', 'military', 'حرب', 'صراع'],
    'عقوبات': ['sanctions', 'embargo', 'ban', 'عقوبات', 'عقوبة'],
    'سوق': ['stock market', 'dow jones', 'nasdaq', 's&p', 'trading', 'سوق', 'أسهم']
}

sent_articles = set()

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def get_user_agent():
    """إرجاع User-Agent عشوائي"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0'
    ]
    return random.choice(user_agents)

def fetch_investing_rss():
    """جلب أخبار Investing.com عبر RSS"""
    all_articles = []
    
    for feed in INVESTING_RSS_FEEDS:
        try:
            logger.info(f"📡 Investing.com: {feed['name']}")
            
            headers = {
                'User-Agent': get_user_agent(),
                'Accept': 'application/rss+xml, text/xml, application/xml',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://www.investing.com/',
                'DNT': '1'
            }
            
            # محاولة جلب RSS
            response = requests.get(feed['url'], headers=headers, timeout=15)
            
            if response.status_code == 200:
                # تحليل RSS
                feed_data = feedparser.parse(response.content)
                
                if feed_data.entries:
                    for entry in feed_data.entries[:8]:  # أول 8 أخبار
                        try:
                            title = entry.get('title', '').strip()
                            link = entry.get('link', '').strip()
                            published = entry.get('published', '')
                            summary = entry.get('summary', entry.get('description', '')).strip()[:200]
                            
                            if not title or not link:
                                continue
                            
                            # تصنيف الخبر
                            category = categorize_news(title + " " + summary)
                            
                            # معرّف فريد
                            article_id = hashlib.md5(f"{title}{link}".encode()).hexdigest()[:12]
                            
                            article = {
                                'id': article_id,
                                'title': title,
                                'link': link,
                                'summary': summary,
                                'source': 'Investing.com',
                                'feed_category': feed['category'],
                                'news_category': category,
                                'published': published or datetime.now().strftime("%Y-%m-%d %H:%M"),
                                'via': 'RSS'
                            }
                            
                            all_articles.append(article)
                            
                        except Exception as e:
                            logger.debug(f"خطأ في معالجة خبر: {e}")
                            continue
                    
                    logger.info(f"   ✅ {len(feed_data.entries)} خبر من {feed['name']}")
                else:
                    logger.warning(f"   ⚠️ لا توجد أخبار في {feed['name']}")
            else:
                logger.warning(f"   ❌ حالة HTTP: {response.status_code} لـ {feed['name']}")
            
            # انتظار عشوائي
            time.sleep(random.uniform(2, 4))
            
        except Exception as e:
            logger.error(f"❌ خطأ في {feed['name']}: {e}")
            continue
    
    return all_articles

def fetch_backup_rss():
    """جلب أخبار من مصادر احتياطية"""
    all_articles = []
    
    for feed in BACKUP_RSS_FEEDS:
        try:
            logger.info(f"📡 احتياطي: {feed['name']}")
            
            feed_data = feedparser.parse(feed['url'])
            
            if feed_data.entries:
                for entry in feed_data.entries[:5]:  # أول 5 أخبار
                    try:
                        title = entry.get('title', '').strip()
                        link = entry.get('link', '').strip()
                        published = entry.get('published', '')
                        summary = entry.get('summary', entry.get('description', '')).strip()[:200]
                        
                        if not title or not link:
                            continue
                        
                        # تصنيف
                        category = categorize_news(title + " " + summary)
                        
                        article_id = hashlib.md5(f"{title}{link}".encode()).hexdigest()[:12]
                        
                        article = {
                            'id': article_id,
                            'title': title,
                            'link': link,
                            'summary': summary,
                            'source': feed['name'],
                            'feed_category': feed['category'],
                            'news_category': category,
                            'published': published or datetime.now().strftime("%Y-%m-%d %H:%M"),
                            'via': 'Backup RSS'
                        }
                        
                        all_articles.append(article)
                        
                    except:
                        continue
                
                logger.info(f"   ✅ {len(feed_data.entries)} خبر من {feed['name']}")
            
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"❌ خطأ في مصدر احتياطي: {e}")
            continue
    
    return all_articles

def categorize_news(text):
    """تصنيف الخبر بناءً على الكلمات المفتاحية"""
    text_lower = text.lower()
    
    for category, keywords in KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return category
    
    return "عام"

def filter_important_news(articles):
    """تصفية الأخبار المهمة فقط"""
    important = []
    
    for article in articles:
        # تجاهل الفئة "عام" وأخذ البقية
        if article['news_category'] != 'عام':
            important.append(article)
    
    return important

def send_telegram_message(bot, article):
    """إرسال خبر إلى قناة تليجرام"""
    try:
        # إيموجيات حسب التصنيف
        emoji_map = {
            'فائدة': '🏦',
            'تضخم': '📈',
            'بطالة': '👥',
            'ناتج': '📊',
            'نفط': '🛢️',
            'ذهب': '💰',
            'حرب': '⚔️',
            'عقوبات': '🚫',
            'سوق': '📊',
            'عام': '📰'
        }
        
        emoji = emoji_map.get(article['news_category'], '📰')
        
        # تنسيق الرسالة
        if any(keyword in article['title'].lower() for keyword in ['عربي', 'العربية', 'الشرق', 'دبي', 'رياض']):
            # إذا كان الخبر عربي
            message = f"""
{emoji} **{article['news_category'].upper()}** | {article['feed_category']} {emoji}

{article['title']}

{article['summary']}

📰 المصدر: {article['source']}
⏰ {article['published']}

🔗 [قراءة الخبر]({article['link']})
            """
        else:
            # إذا كان الخبر إنجليزي
            message = f"""
{emoji} **{article['news_category'].upper()}** | {article['feed_category']} {emoji}

{article['title']}

{article['summary']}

📰 Source: {article['source']}
⏰ {article['published']}

🔗 [Read more]({article['link']})
            """
        
        # إرسال الرسالة
        bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message,
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logger.info(f"✅ تم إرسال: {article['title'][:50]}...")
        sent_articles.add(article['id'])
        return True
        
    except TelegramError as e:
        logger.error(f"❌ خطأ تليجرام: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ خطأ غير متوقع: {e}")
        return False

def main_cycle():
    """الدورة الرئيسية للفحص والإرسال"""
    try:
        logger.info("=" * 60)
        logger.info("🔄 بدء دورة فحص Investing.com")
        
        # 1. الاتصال بالبوت
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        
        # 2. محاولة جلب أخبار Investing.com أولاً
        logger.info("📥 محاولة جلب أخبار Investing.com...")
        investing_articles = fetch_investing_rss()
        
        if investing_articles:
            logger.info(f"✅ Investing.com: {len(investing_articles)} خبر")
            all_articles = investing_articles
        else:
            logger.warning("⚠️ فشل جلب أخبار Investing.com، استخدام المصادر الاحتياطية")
            all_articles = fetch_backup_rss()
        
        # 3. تصفية الأخبار المهمة
        important_news = filter_important_news(all_articles)
        logger.info(f"⭐ الأخبار المهمة: {len(important_news)}")
        
        if not important_news:
            logger.info("ℹ️ لا توجد أخبار مهمة جديدة")
            return
        
        # 4. إرسال الأخبار الجديدة فقط
        new_count = 0
        for article in important_news[:4]:  # أول 4 أخبار فقط
            if article['id'] not in sent_articles:
                success = send_telegram_message(bot, article)
                if success:
                    new_count += 1
                    time.sleep(random.uniform(3, 6))  # انتظار عشوائي
        
        # 5. الإحصائيات
        logger.info(f"📤 تم إرسال {new_count} خبر جديد")
        logger.info(f"💾 في الذاكرة: {len(sent_articles)} خبر")
        
        # 6. تنظيف الذاكرة كل فترة
        if len(sent_articles) > 100:
            sent_articles.clear()
            logger.info("🧹 تم تنظيف الذاكرة")
        
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"🚨 خطأ في الدورة الرئيسية: {e}")

def start_bot():
    """بدء تشغيل البوت"""
    logger.info("=" * 60)
    logger.info("🚀 بدء تشغيل بوت Investing.com للأخبار")
    logger.info(f"📢 القناة: {CHANNEL_USERNAME}")
    logger.info(f"⏰ فترة الفحص: {CHECK_INTERVAL} ثانية")
    logger.info(f"📡 مصادر Investing.com: {len(INVESTING_RSS_FEEDS)}")
    logger.info(f"📡 مصادر احتياطية: {len(BACKUP_RSS_FEEDS)}")
    logger.info("=" * 60)
    
    # اختبار الاتصال بالبوت
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot_info = bot.get_me()
        logger.info(f"🤖 البوت جاهز: @{bot_info.username}")
        
        # رسالة بدء التشغيل
        try:
            bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text="✅ **بوت Investing.com للأخبار الاقتصادية يعمل الآن!**\n\nسيتم إرسال آخر الأخبار الاقتصادية والمالية تلقائياً.\n\n📌 الأنواع المتابعة:\n• قرارات الفائدة والبنوك المركزية\n• بيانات التضخم والوظائف\n• الناتج المحلي والنمو الاقتصادي\n• أسعار النفط والذهب\n• الأخبار الجيوسياسية والأسواق",
                parse_mode='Markdown'
            )
        except:
            logger.warning("⚠️ لم أتمكن من إرسال رسالة البداية")
        
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بالبوت: {e}")
        return False
    
    return True

if __name__ == "__main__":
    # التحقق من الإعدادات
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ خطأ: TELEGRAM_BOT_TOKEN غير محدد")
        logger.error("أضف التوكن في Environment Variables على Render")
        exit(1)
    
    if not CHANNEL_USERNAME:
        logger.error("❌ خطأ: CHANNEL_USERNAME غير محدد")
        exit(1)
    
    # بدء البوت
    if not start_bot():
        exit(1)
    
    # الدورة الرئيسية
    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            logger.info(f"🔁 الدورة رقم: {cycle_count}")
            
            main_cycle()
            
            logger.info(f"⏳ انتظار {CHECK_INTERVAL} ثانية للدورة القادمة...")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("👋 إيقاف البوت...")
            break
        except Exception as e:
            logger.error(f"💥 خطأ غير متوقع في الدورة: {e}")
            time.sleep(300)  # انتظار 5 دقائق ثم معاودة المحاولة
