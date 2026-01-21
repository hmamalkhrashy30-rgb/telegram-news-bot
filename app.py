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
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '300'))  # 5 دقائق

# تأكد من وجود التوكن
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN غير محدد. أضفه في متغيرات البيئة على Render.")

# ========== Flask App للتحقق من أن الخدمة تعمل ==========
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "Telegram News Bot",
        "channel": CHANNEL_USERNAME
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

# ========== نفس كود جلب الأخبار (من النسخة السابقة) ==========
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

async def fetch_news(session, url, category):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    try:
        async with session.get(url, headers=headers, timeout=10) as response:
            if response.status == 200:
                html = await response.text()
                return parse_news(html, category)
    except Exception as e:
        logging.error(f"خطأ في جلب أخبار {category}: {e}")
    return []

def parse_news(html, category):
    soup = BeautifulSoup(html, 'html.parser')
    articles = []
    
    # تحديث: البحث عن العناصر الصحيحة في Investing.com
    news_items = soup.find_all('article', class_='js-article-item')
    
    if not news_items:
        news_items = soup.find_all('div', class_=['mediumTitle1', 'articleItem'])
    
    for item in news_items[:10]:
        try:
            title_elem = item.find('a', class_='title')
            if not title_elem:
                continue
                
            title = title_elem.text.strip()
            link = title_elem.get('href', '')
            
            if link and not link.startswith('http'):
                link = f"https://www.investing.com{link}"
            
            time_elem = item.find('time')
            time_text = time_elem.text.strip() if time_elem else "قبل قليل"
            
            # تصنيف الخبر
            news_type = "عام"
            for type_name, keywords in KEYWORDS.items():
                for keyword in keywords:
                    if keyword.lower() in title.lower():
                        news_type = type_name
                        break
            
            article_data = {
                'title': title,
                'link': link,
                'time': time_text,
                'category': category,
                'type': news_type,
                'unique_id': hash(f"{title[:30]}{time_text}")
            }
            
            articles.append(article_data)
        except Exception as e:
            continue
    
    return articles

def filter_important_news(articles):
    important = []
    for article in articles:
        if article['type'] != "عام":
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
        
        message = f"""
{emoji} **{article['type'].upper()}** {emoji}

📌 {article['title']}

⏰ {article['time']}
🏷️ {article['category']}

🔗 [قراءة الخبر كاملاً]({article['link']})
        """
        
        await bot.send_message(
            chat_id=CHANNEL_USERNAME,
            text=message,
            parse_mode='Markdown',
            disable_web_page_preview=False
        )
        
        logging.info(f"✅ تم إرسال: {article['title'][:50]}...")
        sent_articles.add(article['unique_id'])
        
    except Exception as e:
        logging.error(f"❌ خطأ في إرسال الرسالة: {e}")

# ========== الدورة الرئيسية في thread منفصل ==========
async def news_check_loop():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                logging.info("🔄 بدء فحص الأخبار...")
                
                all_articles = []
                tasks = []
                
                for category, url in NEWS_URLS.items():
                    tasks.append(fetch_news(session, url, category))
                
                results = await asyncio.gather(*tasks)
                
                for result in results:
                    all_articles.extend(result)
                
                important_news = filter_important_news(all_articles)
                
                # إرسال الأخبار الجديدة فقط
                new_count = 0
                for article in important_news:
                    if article['unique_id'] not in sent_articles:
                        await send_telegram_message(bot, article)
                        new_count += 1
                        await asyncio.sleep(1)
                
                if len(sent_articles) > 1000:
                    sent_articles.clear()
                
                if new_count > 0:
                    logging.info(f"📤 تم إرسال {new_count} خبر جديد")
                else:
                    logging.info("⚠️ لا توجد أخبار جديدة")
                
            except Exception as e:
                logging.error(f"🚨 خطأ في الدورة الرئيسية: {e}")
            
            await asyncio.sleep(CHECK_INTERVAL)

def start_bot_thread():
    """تشغيل البوت في thread منفصل"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(news_check_loop())

# ========== بدء التشغيل عند تشغيل الخدمة ==========
@app.before_first_request
def start_background_thread():
    """بدء thread البوت عند تشغيل الخدمة"""
    thread = threading.Thread(target=start_bot_thread, daemon=True)
    thread.start()
    logging.info("🤖 تم بدء بوت الأخبار في الخلفية")

# ========== نقطة الدخول الرئيسية ==========
if __name__ == "__main__":
    # إعداد التسجيل
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    logging.info("🚀 بدء تشغيل بوت أخبار الأسواق على Render...")
    
    # بدء Flask app
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
