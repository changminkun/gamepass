import feedparser
import smtplib
import json
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import re
import logging
import time
import requests
from requests.exceptions import RequestException
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import textwrap
from urllib.parse import urlparse, urlunparse

class GamePassNotifier:
    def __init__(self, smtp_client=None, feed_parser=None, file_handler=None):
        self.setup_logging()
        self.smtp_client = smtp_client or smtplib.SMTP
        self.feed_parser = feed_parser or feedparser
        self.file_handler = file_handler or {'load': self.load_seen_articles, 'save': self.save_seen_articles}
        
        # 환경 변수 유효성 검사 강화
        required_envs = ['SMTP_SERVER', 'SMTP_PORT', 'SENDER_EMAIL', 'SENDER_PASSWORD', 'RECEIVER_EMAIL']
        missing = [env for env in required_envs if not os.environ.get(env)]
        if missing:
            raise ValueError(f"다음 환경 변수가 누락되었습니다: {', '.join(missing)}")
        
        self.smtp_server = os.environ['SMTP_SERVER']
        try:
            self.smtp_port = int(os.environ['SMTP_PORT'])
            if self.smtp_port <= 0:
                raise ValueError("SMTP_PORT는 양수여야 합니다.")
        except ValueError:
            raise ValueError("SMTP_PORT는 유효한 숫자 형식이어야 합니다.")
        
        self.sender_email = os.environ['SENDER_EMAIL']
        self.sender_password = os.environ['SENDER_PASSWORD']
        self.receiver_email = os.environ['RECEIVER_EMAIL']
        
        self.rss_url = "https://news.xbox.com/en-us/feed/"
        self.seen_articles_file = "seen_articles.json"
        self.config = self.load_config()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler('gamepass_notifier.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def load_config(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"설정 파일 로드 실패: {e}")
            return {
                "gamepass_keywords": [
                    "game pass", "gamepass", "xbox game pass", "pc game pass",
                    "coming to game pass", "leaving game pass",
                    "available now on game pass", "joins game pass",
                    "say goodbye", "day one", "hollow knight", "silksong"
                ],
                "add_patterns": [
                    r'coming to (?:xbox )?game pass',
                    r'available (?:now )?(?:on|in) (?:xbox )?game pass',
                    r'joins? (?:xbox )?game pass',
                    r'new.*(?:xbox )?game pass',
                    r'day one (?:on|with) (?:xbox )?game pass',
                    r'added to (?:xbox )?game pass'
                ],
                "remove_patterns": [
                    r'leaving (?:xbox )?game pass',
                    r'last chance.*(?:xbox )?game pass',
                    r'say goodbye',
                    r'final days',
                    r'removed from (?:xbox )?game pass'
                ]
            }

    def load_email_template(self, lang='ko'):
        templates = {
            'ko': {
                'subject': "🎮 Game Pass 업데이트 알림 - {count}개 소식",
                'header': "🎮 Xbox Game Pass",
                'subheader': "새로운 업데이트가 있습니다!",
                'stats': "📊 총 {count}개의 새로운 소식",
                'footer': "🤖 GitHub Actions 자동 알림<br>매일 한국 시간 오전 9시에 자동으로 확인합니다.<br>Game Pass 게임 목록 변화만 선별하여 알려드립니다."
            },
            'en': {
                'subject': "🎮 Game Pass Update - {count} New Items",
                'header': "🎮 Xbox Game Pass",
                'subheader': "New updates are here!",
                'stats': "📊 {count} new updates",
                'footer': "🤖 Automated GitHub Actions Notification<br>Checked daily at 9 AM KST.<br>Curated updates for Game Pass changes."
            }
        }
        return templates.get(lang, templates['ko'])

    def fetch_rss_feed(self, retries=3, delay=5):
        for attempt in range(retries):
            try:
                response = requests.head(self.rss_url, timeout=5)
                if response.status_code != 200:
                    raise RequestException(f"RSS 피드 URL 상태 코드: {response.status_code}")
                feed = self.feed_parser.parse(self.rss_url)
                if feed.bozo:
                    raise ValueError(f"RSS 피드 파싱 오류: {feed.bozo_exception}")
                self.logger.info(f"RSS 피드에서 {len(feed.entries)}개 기사 발견: {[entry.title for entry in feed.entries]}")
                return feed
            except (RequestException, ValueError) as e:
                self.logger.error(f"RSS 피드 가져오기 실패 (시도 {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)
        raise Exception("RSS 피드를 가져올 수 없습니다.")

    def normalize_url(self, url):
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

    def truncate_summary(self, summary, max_length=300):
        if len(summary) <= max_length:
            return summary
        sentences = summary.split('. ')
        truncated = ''
        for sentence in sentences:
            if len(truncated + sentence + '. ') <= max_length:
                truncated += sentence + '. '
            else:
                break
        return truncated.rstrip() + '...' if truncated else summary[:max_length] + '...'

    def load_seen_articles(self):
        try:
            if os.path.exists(self.seen_articles_file):
                with open(self.seen_articles_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cutoff = (datetime.now() - timedelta(days=30)).timestamp()
                    return set(link for link, timestamp in data.items() 
                               if datetime.fromtimestamp(timestamp).timestamp() > cutoff)
        except Exception as e:
            self.logger.error(f"기존 데이터 로드 실패: {e}")
        return set()

    def save_seen_articles(self, seen_articles):
        try:
            data = {link: datetime.now().timestamp() for link in seen_articles}
            with open(self.seen_articles_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"확인한 기사 {len(seen_articles)}개 저장 완료")
        except Exception as e:
            self.logger.error(f"파일 저장 오류: {e}")

    def is_gamepass_related(self, title, summary):
        text = (title + " " + summary).lower()
        is_related = any(keyword in text for keyword in self.config['gamepass_keywords'])
        self.logger.info(f"기사: {title[:50]}... Game Pass 관련={is_related}")
        return is_related

    def extract_game_info(self, title, summary):
        text = (title + " " + summary).lower()
        is_addition = any(re.search(pattern, text) for pattern in self.config['add_patterns'])
        is_removal = any(re.search(pattern, text) for pattern in self.config['remove_patterns'])
        self.logger.info(f"기사: {title[:50]}... 추가={is_addition}, 제거={is_removal}")
        return is_addition, is_removal

    def process_article(self, entry, seen_articles):
        article_id = self.normalize_url(entry.link)
        if article_id in seen_articles:
            self.logger.info(f"⏭️ 중복 기사 스킵: {entry.title[:50]}... ({article_id})")
            return None
        if not self.is_gamepass_related(entry.title, entry.summary):
            self.logger.info(f"🚫 Game Pass 관련 아님: {entry.title[:50]}...")
            return None
        is_addition, is_removal = self.extract_game_info(entry.title, entry.summary)
        return {
            'title': entry.title,
            'link': entry.link,
            'published': getattr(entry, 'published', '날짜 불명'),
            'summary': self.truncate_summary(entry.summary),
            'is_addition': is_addition,
            'is_removal': is_removal
        }

    def create_email_content(self, articles):
        lang = os.environ.get('LANGUAGE', 'ko')
        template = self.load_email_template(lang)
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; }}
                .container {{ max-width: 600px; margin: 0 auto; }}
                .header {{ background: linear-gradient(135deg, #107C10, #0E6B0E); color: white; padding: 30px 20px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 28px; }}
                .header p {{ margin: 10px 0 0; opacity: 0.9; }}
                .content {{ padding: 0 20px; }}
                .article {{ background: #fff; margin: 20px 0; padding: 20px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); border-left: 4px solid #107C10; }}
                .article-title {{ font-size: 20px; font-weight: 600; margin-bottom: 12px; color: #107C10; line-height: 1.3; }}
                .article-meta {{ font-size: 13px; color: #666; margin-bottom: 12px; }}
                .tags {{ margin-bottom: 15px; }}
                .tag {{ display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 500; margin-right: 8px; }}
                .tag-addition {{ background: #d1f2d1; color: #0f5132; }}
                .tag-removal {{ background: #f8d7da; color: #842029; }}
                .article-summary {{ margin-bottom: 15px; color: #555; line-height: 1.5; }}
                .article-link {{ display: inline-block; color: #107C10; text-decoration: none; font-weight: 600; padding: 8px 16px; border: 2px solid #107C10; border-radius: 6px; transition: all 0.3s; }}
                .article-link:hover {{ background: #107C10; color: white; }}
                .footer {{ text-align: center; margin: 40px 0; padding: 20px; background: #f8f9fa; border-radius: 8px; }}
                .footer p {{ margin: 5px 0; font-size: 13px; color: #666; }}
                .stats {{ background: #f0f8f0; padding: 15px; border-radius: 8px; margin: 20px 0; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{template['header']}</h1>
                    <p>{template['subheader']}</p>
                </div>
                <div class="content">
                    <div class="stats">
                        <strong>{template['stats'].format(count=len(articles))}</strong><br>
                        <small>{datetime.now().strftime('%Y년 %m월 %d일')}</small>
                    </div>
        """
        
        for article in articles:
            tags_html = ""
            if article['is_addition']:
                tags_html += '<span class="tag tag-addition">✅ 게임 추가</span>'
            if article['is_removal']:
                tags_html += '<span class="tag tag-removal">⏰ 게임 제거</span>'
            
            html += f"""
                    <div class="article">
                        <div class="article-title">{article['title']}</div>
                        <div class="article-meta">📅 {article['published']}</div>
                        <div class="tags">{tags_html}</div>
                        <div class="article-summary">{article['summary']}</div>
                        <a href="{article['link']}" class="article-link">전체 기사 보기 →</a>
                    </div>
            """
        
        html += f"""
                    <div class="footer">
                        <p>{template['footer']}</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        try:
            BeautifulSoup(html, 'html.parser')  # HTML 유효성 검사
        except Exception as e:
            self.logger.error(f"HTML 파싱 오류: {e}")
            raise
        return html

    def send_email(self, articles, retries=3, delay=5):
        for attempt in range(retries):
            try:
                msg = MIMEMultipart('alternative')
                msg['Subject'] = f"🎮 Game Pass 업데이트 알림 - {len(articles)}개 소식"
                msg['From'] = self.sender_email
                msg['To'] = self.receiver_email
                
                html_content = self.create_email_content(articles)
                html_part = MIMEText(html_content, 'html', 'utf-8')
                msg.attach(html_part)
                
                with self.smtp_client(self.smtp_server, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.sender_email, self.sender_password)
                    server.send_message(msg)
                    
                self.logger.info(f"✅ 이메일 발송 성공: {len(articles)}개 기사")
                return True
                
            except Exception as e:
                self.logger.error(f"❌ 이메일 발송 실패 (시도 {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)
        self.logger.error("최대 재시도 횟수 초과")
        return False

    def run(self):
        self.logger.info("🔍 Game Pass RSS 피드 확인 시작...")
        
        try:
            seen_articles = self.load_seen_articles()
            self.logger.info(f"📚 기존 확인한 기사: {len(seen_articles)}개")
            
            feed = self.fetch_rss_feed()
            new_articles = []
            
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = executor.map(lambda entry: self.process_article(entry, seen_articles), feed.entries[:50])
                new_articles = [result for result in results if result]
            
            for article in new_articles:
                seen_articles.add(self.normalize_url(article['link']))
            
            if new_articles:
                self.logger.info(f"📧 {len(new_articles)}개 새 기사 발견, 이메일 발송 중...")
                self.send_email(new_articles)
            else:
                self.logger.info("📭 새로운 Game Pass 소식 없음")
            
            self.save_seen_articles(seen_articles)
            self.logger.info("✅ 처리 완료!")
                
        except Exception as e:
            self.logger.error(f"❌ 실행 중 오류: {e}")

if __name__ == "__main__":
    notifier = GamePassNotifier()
    notifier.run()
