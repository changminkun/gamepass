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
from urllib.parse import urlparse, urlunparse
import xml.etree.ElementTree as ET
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

class GamePassNotifier:
    def __init__(self, smtp_client=None, feed_parser=None, file_handler=None):
        self.setup_logging()
        self.smtp_client = smtp_client or smtplib.SMTP
        self.feed_parser = feed_parser or feedparser
        self.file_handler = file_handler or {'load': self.load_seen_articles, 'save': self.save_seen_articles}
        
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
        
        self.rss_urls = [
            "https://news.xbox.com/en-us/feed/",
            "https://news.xbox.com/en-us/xbox-game-pass/"
        ]
        self.seen_articles_file = "seen_articles.json"
        self.config = self.load_config()
        self.save_seen_articles(set())  # 초기 빈 파일 생성

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

    def fetch_rss_feed(self, retries=3, delay=5):
        all_entries = []
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        for url in self.rss_urls:
            for attempt in range(retries):
                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    response.raise_for_status()
                    try:
                        ET.fromstring(response.text)  # XML 유효성 검사
                        self.logger.debug(f"RSS 피드 응답 (처음 500자): {response.text[:500]}")
                    except ET.ParseError as xml_err:
                        self.logger.error(f"RSS 피드 XML 파싱 오류 ({url}): {xml_err}")
                        raise ValueError(f"XML 파싱 오류: {xml_err}")
                    feed = self.feed_parser.parse(response.text, request_headers=headers)
                    if feed.bozo:
                        raise ValueError(f"RSS 피드 파싱 오류 ({url}): {feed.bozo_exception}")
                    self.logger.info(f"📡 {url}에서 {len(feed.entries)}개 기사 발견: {[entry.title for entry in feed.entries]}")
                    all_entries.extend(feed.entries)
                    break
                except (RequestException, ValueError) as e:
                    self.logger.error(f"RSS 피드 가져오기 실패 ({url}, 시도 {attempt + 1}/{retries}): {e}")
                    if attempt < retries - 1:
                        time.sleep(delay)
                    else:
                        self.logger.warning(f"{url}에서 데이터 가져오기 최종 실패")
        return {'entries': all_entries} if all_entries else None

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
                    if isinstance(data, list):
                        self.logger.warning("seen_articles.json이 리스트 형식이므로 초기화합니다.")
                        return set()
                    cutoff = (datetime.now() - timedelta(days=30)).timestamp()
                    return set(link for link, timestamp in data.items() 
                               if isinstance(timestamp, (int, float)) and datetime.fromtimestamp(timestamp).timestamp() > cutoff)
            return set()
        except Exception as e:
            self.logger.error(f"기존 데이터 로드 실패: {e}")
            return set()

    def save_seen_articles(self, seen_articles):
        try:
            data = {link: datetime.now().timestamp() for link in seen_articles}
            with open(self.seen_articles_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"✅ 확인한 기사 {len(seen_articles)}개 저장 완료")
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
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; }}
                .container {{ max-width: 600px; margin: 0 auto; }}
                .header {{ background: #107C10; color: white; padding: 20px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 24px; }}
                .header p {{ margin: 5px 0 0; opacity: 0.9; }}
                .content {{ padding: 0 20px; }}
                .article {{ background: #fff; margin: 15px 0; padding: 15px; border-radius: 8px; border-left: 4px solid #107C10; }}
                .article-title {{ font-size: 18px; font-weight: 600; margin-bottom: 10px; color: #107C10; }}
                .article-meta {{ font-size: 12px; color: #666; margin-bottom: 10px; }}
                .tags {{ margin-bottom: 10px; }}
                .tag {{ display: inline-block; padding: 4px 10px; border-radius: 15px; font-size: 11px; font-weight: 500; margin-right: 5px; }}
                .tag-addition {{ background: #d1f2d1; color: #0f5132; }}
                .tag-removal {{ background: #f8d7da; color: #842029; }}
                .article-summary {{ margin-bottom: 10px; color: #555; line-height: 1.5; }}
                .article-link {{ color: #107C10; text-decoration: none; font-weight: 600; padding: 6px 12px; border: 2px solid #107C10; border-radius: 5px; }}
                .article-link:hover {{ background: #107C10; color: white; }}
                .footer {{ text-align: center; margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 8px; }}
                .footer p {{ margin: 5px 0; font-size: 12px; color: #666; }}
                .stats {{ background: #f0f8f0; padding: 10px; border-radius: 8px; margin: 15px 0; text-align: center; }}
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
        if BeautifulSoup:
            try:
                BeautifulSoup(html, 'html.parser')
            except Exception as e:
                self.logger.error(f"HTML 파싱 오류: {e}")
                raise
        return html

    def load_email_template(self, lang='ko'):
        templates = {
            'ko': {
                'subject': "🎮 Game Pass 업데이트 알림 - {count}개 소식",
                'header': "🎮 Xbox Game Pass",
                'subheader': "새로운 업데이트가 있습니다!",
                'stats': "📊 총 {count}개의 새로운 소식",
                'footer': "🤖 GitHub Actions 자동 알림<br>매일 한국 시간 오전 9시, 오후 3시, 오후 9시에 자동으로 확인합니다.<br>Game Pass 게임 목록 변화만 선별하여 알려드립니다."
            },
            'en': {
                'subject': "🎮 Game Pass Update - {count} New Items",
                'header': "🎮 Xbox Game Pass",
                'subheader': "New updates are here!",
                'stats': "📊 {count} new updates",
                'footer': "🤖 Automated GitHub Actions Notification<br>Checked daily at 9 AM, 3 PM, 9 PM KST.<br>Curated updates for Game Pass changes."
            }
        }
        return templates.get(lang, templates['ko'])

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
                with self.smtp_client(self.smtp_server, self.smtp_port, timeout=10) as server:
                    server.starttls()
                    server.login(self.sender_email, self.sender_password)
                    server.send_message(msg)
                self.logger.info(f"✅ 이메일 발송 성공: {len(articles)}개 기사")
                return True
            except smtplib.SMTPException as smtp_err:
                self.logger.error(f"❌ SMTP 오류 (시도 {attempt + 1}/{retries}): {smtp_err}")
                if attempt < retries - 1:
                    time.sleep(delay)
            except Exception as e:
                self.logger.error(f"❌ 이메일 발송 중 기타 오류 (시도 {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(delay)
        self.logger.error("❌ 최대 재시도 횟수 초과")
        return False

    def run(self):
        self.logger.info("🔍 Game Pass RSS 피드 확인 시작...")
        try:
            seen_articles = self.load_seen_articles()
            self.logger.info(f"📚 기존 확인한 기사: {len(seen_articles)}개")
            feed = self.fetch_rss_feed()
            if not feed or not feed.entries:
                self.logger.warning("📭 RSS 피드 가져오기 실패 또는 빈 피드, 처리 중단")
                self.save_seen_articles(seen_articles)
                return
            new_articles = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = executor.map(lambda entry: self.process_article(entry, seen_articles), feed.entries[:50])
                new_articles = [result for result in results if result]
            for article in new_articles:
                seen_articles.add(self.normalize_url(article['link']))
            if new_articles:
                self.logger.info(f"📧 {len(new_articles)}개 새 기사 발견, 이메일 발송 중...")
                if self.send_email(new_articles):
                    self.logger.info("✅ 이메일 발송 성공")
                else:
                    self.logger.error("❌ 이메일 발송 실패")
            else:
                self.logger.info("📭 새로운 Game Pass 소식 없음")
            self.save_seen_articles(seen_articles)
            self.logger.info("✅ 처리 완료!")
        except Exception as e:
            self.logger.error(f"❌ 실행 중 오류: {e}")
            self.save_seen_articles(seen_articles)  # 오류 발생 시에도 저장

    def test_email(self):
        test_article = [{
            'title': 'Hollow Knight: Silksong Available Day One on Game Pass',
            'link': 'https://news.xbox.com/en-us/2025/08/21/xbox-at-gamescom-2025/',
            'published': '2025-08-21',
            'summary': 'Hollow Knight: Silksong will be available day one on Xbox Game Pass.',
            'is_addition': True,
            'is_removal': False
        }]
        self.send_email(test_article)

if __name__ == "__main__":
    notifier = GamePassNotifier()
    notifier.run()
