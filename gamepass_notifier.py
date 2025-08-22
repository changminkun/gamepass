import feedparser
import smtplib
import json
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import re

class GamePassNotifier:
    def __init__(self):
        # GitHub Secrets에서 환경변수 읽기
        self.smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', '587'))
        self.sender_email = os.environ.get('SENDER_EMAIL')
        self.sender_password = os.environ.get('SENDER_PASSWORD')
        self.receiver_email = os.environ.get('RECEIVER_EMAIL')
        
        self.rss_url = "https://news.xbox.com/en-us/feed/"
        self.seen_articles_file = "seen_articles.json"
        
    def load_seen_articles(self):
        """GitHub 저장소에서 이전 기사 목록 로드"""
        try:
            if os.path.exists(self.seen_articles_file):
                with open(self.seen_articles_file, 'r', encoding='utf-8') as f:
                    return set(json.load(f))
        except Exception as e:
            print(f"기존 데이터 로드 실패: {e}")
        return set()
    
    def save_seen_articles(self, seen_articles):
        """확인한 기사 목록 저장"""
        try:
            with open(self.seen_articles_file, 'w', encoding='utf-8') as f:
                json.dump(list(seen_articles), f, indent=2)
            print(f"확인한 기사 {len(seen_articles)}개 저장 완료")
        except Exception as e:
            print(f"파일 저장 오류: {e}")
    
    def is_gamepass_related(self, title, summary):
        """Game Pass 관련 기사인지 확인"""
        gamepass_keywords = [
            'game pass', 'gamepass', 'xbox game pass', 
            'coming to game pass', 'leaving game pass',
            'available now on game pass', 'pc game pass',
            'joins game pass', 'say goodbye'
        ]
        
        text = (title + " " + summary).lower()
        return any(keyword in text for keyword in gamepass_keywords)
    
    def extract_game_info(self, title, summary):
        """게임 추가/제거 정보 추출"""
        text = (title + " " + summary).lower()
        
        # 게임 추가 패턴
        add_patterns = [
            r'coming to (?:xbox )?game pass',
            r'available (?:now )?(?:on|in) (?:xbox )?game pass',
            r'joins? (?:xbox )?game pass',
            r'new.*(?:xbox )?game pass'
        ]
        
        # 게임 제거 패턴
        remove_patterns = [
            r'leaving (?:xbox )?game pass',
            r'last chance.*(?:xbox )?game pass',
            r'say goodbye',
            r'final days'
        ]
        
        is_addition = any(re.search(pattern, text) for pattern in add_patterns)
        is_removal = any(re.search(pattern, text) for pattern in remove_patterns)
        
        return is_addition, is_removal
    
    def create_email_content(self, articles):
        """이메일 HTML 내용 생성"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; }
                .container { max-width: 600px; margin: 0 auto; }
                .header { background: linear-gradient(135deg, #107C10, #0E6B0E); color: white; padding: 30px 20px; text-align: center; }
                .header h1 { margin: 0; font-size: 28px; }
                .header p { margin: 10px 0 0; opacity: 0.9; }
                .content { padding: 0 20px; }
                .article { background: #fff; margin: 20px 0; padding: 20px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); border-left: 4px solid #107C10; }
                .article-title { font-size: 20px; font-weight: 600; margin-bottom: 12px; color: #107C10; line-height: 1.3; }
                .article-meta { font-size: 13px; color: #666; margin-bottom: 12px; }
                .tags { margin-bottom: 15px; }
                .tag { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 500; margin-right: 8px; }
                .tag-addition { background: #d1f2d1; color: #0f5132; }
                .tag-removal { background: #f8d7da; color: #842029; }
                .article-summary { margin-bottom: 15px; color: #555; line-height: 1.5; }
                .article-link { display: inline-block; color: #107C10; text-decoration: none; font-weight: 600; padding: 8px 16px; border: 2px solid #107C10; border-radius: 6px; transition: all 0.3s; }
                .article-link:hover { background: #107C10; color: white; }
                .footer { text-align: center; margin: 40px 0; padding: 20px; background: #f8f9fa; border-radius: 8px; }
                .footer p { margin: 5px 0; font-size: 13px; color: #666; }
                .stats { background: #f0f8f0; padding: 15px; border-radius: 8px; margin: 20px 0; text-align: center; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎮 Xbox Game Pass</h1>
                    <p>새로운 업데이트가 있습니다!</p>
                </div>
                <div class="content">
                    <div class="stats">
                        <strong>📊 총 {count}개의 새로운 소식</strong><br>
                        <small>{date}</small>
                    </div>
        """.format(count=len(articles), date=datetime.now().strftime('%Y년 %m월 %d일'))
        
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
        
        html += """
                    <div class="footer">
                        <p><strong>🤖 GitHub Actions 자동 알림</strong></p>
                        <p>매일 한국 시간 오전 9시에 자동으로 확인합니다.</p>
                        <p>Game Pass 게임 목록 변화만 선별하여 알려드립니다.</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    
    def send_email(self, articles):
        """이메일 발송"""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"🎮 Game Pass 업데이트 알림 - {len(articles)}개 소식"
            msg['From'] = self.sender_email
            msg['To'] = self.receiver_email
            
            html_content = self.create_email_content(articles)
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
                
            print(f"✅ 이메일 발송 성공: {len(articles)}개 기사")
            return True
            
        except Exception as e:
            print(f"❌ 이메일 발송 실패: {e}")
            return False
    
    def run(self):
        """메인 실행 함수"""
        print("🔍 Game Pass RSS 피드 확인 시작...")
        
        try:
            # 기존 확인한 기사 로드
            seen_articles = self.load_seen_articles()
            print(f"📚 기존 확인한 기사: {len(seen_articles)}개")
            
            # RSS 피드 파싱
            feed = feedparser.parse(self.rss_url)
            print(f"📡 RSS 피드에서 {len(feed.entries)}개 기사 발견")
            
            new_articles = []
            
            for entry in feed.entries:
                article_id = entry.link
                
                # 이미 확인한 기사는 스킵
                if article_id in seen_articles:
                    continue
                
                # Game Pass 관련 기사만 필터링
                if not self.is_gamepass_related(entry.title, entry.summary):
                    continue
                
                # 게임 추가/제거 정보 추출
                is_addition, is_removal = self.extract_game_info(entry.title, entry.summary)
                
                article_info = {
                    'title': entry.title,
                    'link': entry.link,
                    'published': getattr(entry, 'published', '날짜 불명'),
                    'summary': entry.summary[:300] + "..." if len(entry.summary) > 300 else entry.summary,
                    'is_addition': is_addition,
                    'is_removal': is_removal
                }
                
                new_articles.append(article_info)
                seen_articles.add(article_id)
                print(f"🎯 새 Game Pass 기사 발견: {entry.title[:50]}...")
            
            # 새로운 기사가 있으면 이메일 발송
            if new_articles:
                print(f"📧 {len(new_articles)}개 새 기사 발견, 이메일 발송 중...")
                if self.send_email(new_articles):
                    self.save_seen_articles(seen_articles)
                    print("✅ 처리 완료!")
                else:
                    print("❌ 이메일 발송 실패")
            else:
                print("📭 새로운 Game Pass 소식 없음")
                # 변화가 없어도 seen_articles 업데이트
                self.save_seen_articles(seen_articles)
                
        except Exception as e:
            print(f"❌ 실행 중 오류: {e}")

if __name__ == "__main__":
    notifier = GamePassNotifier()
    notifier.run() 
