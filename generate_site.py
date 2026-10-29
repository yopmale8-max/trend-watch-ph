import os
import re
import json
import feedparser
from datetime import datetime
from google import genai

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
NICHE_CONFIG = {
    "site_name": "TrendWatch Philippines",
    "niche_focus": "Consumer safety recalls, trending public news, tech updates, and viral alerts in the Philippines and globally.",
    "rss_url": "https://trends.google.com/trending/rss?geo=PH",
    "output_dir": "docs" # Netlify default publication folder
}

# Negative keywords to protect domain reputation and ad policy compliance
BLOCKED_KEYWORDS = [
    "suicide", "murder", "shooting", "kill", "death", "tragedy", 
    "accident", "crash", "explicit", "nsfw", "court", "trial"
]

# ==========================================
# INITIALIZE GEMINI CLIENT
# ==========================================
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def is_safe_trend(title):
    """Filters out sensitive or tragic news to comply with AdSense policies."""
    lower_title = title.lower()
    for word in BLOCKED_KEYWORDS:
        if word in lower_title:
            return False
    return True

def fetch_google_trends():
    """Fetches real-time trends from Google Trends RSS feed."""
    print(f"Fetching RSS feed from {NICHE_CONFIG['rss_url']}...")
    feed = feedparser.parse(NICHE_CONFIG['rss_url'])
    
    valid_trends = []
    for entry in feed.entries:
        title = entry.title
        if not is_safe_trend(title):
            print(f"Skipping filtered/sensitive trend: {title}")
            continue
            
        # Extract metadata if available
        news_items = []
        if hasattr(entry, 'ht_news_item'):
            for item in entry.ht_news_item:
                news_items.append({
                    "title": getattr(item, 'ht_news_item_title', ''),
                    "url": getattr(item, 'ht_news_item_url', ''),
                    "source": getattr(item, 'ht_news_item_source', '')
                })
                
        approx_traffic = getattr(entry, 'ht_approx_traffic', 'High Search Volume')
        
        valid_trends.append({
            "title": title,
            "traffic": approx_traffic,
            "news": news_items,
            "slug": re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
        })
        
    return valid_trends[:10] # Process top 10 trends per run

def generate_ai_content(trend):
    """Generates structured FAQ HTML and metadata using Gemini Flash with anti-hallucination guardrails."""
    news_context = " ".join([n['title'] for n in trend['news']])
    
    prompt = f"""
    You are an expert programmatic SEO editor. 
    Trend Title: "{trend['title']}"
    Context News: "{news_context}"
    Approximate Search Volume: "{trend['traffic']}"

    Task: Write a concise, highly informative, professional 300-350 word FAQ page explaining why this topic is currently trending. 
    
    CRITICAL INSTRUCTIONS:
    - If the trend title is completely ambiguous, gibberish, or lacks enough context to construct a factual summary, output ONLY the word: SKIP.
    - Otherwise, return a JSON response with EXACTLY this structure (no markdown code blocks outside JSON, pure string output or valid JSON):
    {{
      "status": "SUCCESS",
      "meta_description": "A 150-character punchy meta description summarizing why {trend['title']} is trending.",
      "html_content": "<h2>Overview</h2><p>...</p><h2>Key Details</h2><p>...</p><h2>Frequently Asked Questions</h2><ul><li><strong>...</strong><span>...</span></li></ul>"
    }}
    """
    
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        
        text_response = response.text.strip()
        if "SKIP" in text_response:
            return None
            
        # Clean up potential markdown formatting from LLM response
        if text_response.startswith("```json"):
            text_response = text_response[7:-3].strip()
        elif text_response.startswith("```"):
            text_response = text_response[3:-3].strip()
            
        return json.loads(text_response)
    except Exception as e:
        print(f"AI Generation Error for {trend['title']}: {e}")
        return None

def build_page_html(trend, ai_data):
    """Builds a high-performance, responsive HTML page with AdSense slots and Schema markup."""
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    schema_json = json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [{
            "@type": "Question",
            "name": f"Why is {trend['title']} trending right now?",
            "acceptedAnswer": {
                "@type": "Answer",
                "text": ai_data['meta_description']
            }
        }]
    })

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{trend['title']} - Live Trend Breakdown | {NICHE_CONFIG['site_name']}</title>
    <meta name="description" content="{ai_data['meta_description']}">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="https://yourdomain.com/{trend['slug']}.html">
    <script type="application/ld+json">{schema_json}</script>
    <style>
        :root {{ --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --accent: #38bdf8; --muted: #94a3b8; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; margin: 0; padding: 0; }}
        header {{ background: var(--card); border-bottom: 1px solid #334155; padding: 1rem 2rem; display: flex; justify-content: space-between; align-items: center; }}
        header a {{ color: var(--accent); text-decoration: none; font-weight: bold; }}
        .container {{ max-width: 800px; margin: 2rem auto; padding: 0 1rem; }}
        .card {{ background: var(--card); border: 1px solid #334155; border-radius: 12px; padding: 2rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
        .meta-badge {{ display: inline-block; background: #0284c7; color: white; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.85rem; font-weight: 500; margin-bottom: 1rem; }}
        .timestamp {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; display: block; }}
        h1 {{ font-size: 2rem; margin-top: 0; letter-spacing: -0.025em; }}
        h2 {{ font-size: 1.25rem; color: var(--accent); margin-top: 1.5rem; border-bottom: 1px solid #334155; padding-bottom: 0.5rem; }}
        p, li {{ color: #cbd5e1; }}
        .ad-slot {{ background: #090d16; border: 1px dashed #334155; padding: 1rem; text-align: center; color: var(--muted); font-size: 0.85rem; border-radius: 8px; margin: 2rem 0; }}
        footer {{ text-align: center; color: var(--muted); padding: 3rem 0; font-size: 0.9rem; border-top: 1px solid #334155; margin-top: 4rem; }}
    </style>
</head>
<body>
    <header>
        <a href="index.html">← {NICHE_CONFIG['site_name']}</a>
        <span style="font-size: 0.85rem; color: var(--muted);">Live Google Trends Analyzer</span>
    </header>

    <div class="container">
        <article class="card">
            <span class="meta-badge">🔥 Volume: {trend['traffic']}</span>
            <h1>{trend['title']}</h1>
            <span class="timestamp">Updated via automated live feed: {current_time}</span>

            <!-- Top AdSense Banner Slot -->
            <div class="ad-slot">
                [Google AdSense Auto-Ad Header Unit]
            </div>

            <div class="content">
                {ai_data['html_content']}
            </div>

            <!-- In-Content AdSense Unit -->
            <div class="ad-slot">
                [Google AdSense In-Article Responsive Ad Unit]
            </div>

            <div style="margin-top: 2rem; border-top: 1px solid #334155; padding-top: 1rem;">
                <h3>Related News Sources</h3>
                <ul>
"""
    for item in trend['news']:
        if item['title'] and item['url']:
            html += f'<li><a href="{item["url"]}" target="_blank" rel="nofollow">{item["title"]}</a> ({item["source"]})</li>'
            
    html += f"""
                </ul>
            </div>
        </article>
    </div>

    <footer>
        <p>&copy; {datetime.utcnow().year} {NICHE_CONFIG['site_name']}. All rights reserved. Automated programmatic publishing.</p>
    </footer>
</body>
</html>
"""
    return html

def update_index_page(generated_trends):
    """Builds a dynamic index.html acting as a live dashboard with internal linking and sitemap integration."""
    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{NICHE_CONFIG['site_name']} - Real-Time Trend Breakdowns</title>
    <meta name="description" content="Explore live real-time trend breakdowns, explanations, and search insights updated automatically around the clock.">
    <style>
        :root {{ --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --accent: #38bdf8; --muted: #94a3b8; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; margin: 0; padding: 0; }}
        header {{ background: var(--card); border-bottom: 1px solid #334155; padding: 2rem; text-align: center; }}
        h1 {{ margin: 0; font-size: 2.25rem; }}
        p.subtitle {{ color: var(--muted); margin-top: 0.5rem; }}
        .container {{ max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
        .trend-grid {{ display: grid; gap: 1rem; }}
        .trend-card {{ background: var(--card); border: 1px solid #334155; border-radius: 8px; padding: 1.5rem; transition: transform 0.2s; }}
        .trend-card:hover {{ transform: translateY(-2px); border-color: var(--accent); }}
        .trend-card a {{ color: var(--text); text-decoration: none; font-size: 1.25rem; font-weight: bold; display: block; margin-bottom: 0.5rem; }}
        .trend-card a:hover {{ color: var(--accent); }}
        .badge {{ background: #0284c7; color: white; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
        .ad-slot {{ background: #090d16; border: 1px dashed #334155; padding: 1rem; text-align: center; color: var(--muted); font-size: 0.85rem; border-radius: 8px; margin: 2rem 0; }}
        footer {{ text-align: center; color: var(--muted); padding: 3rem 0; border-top: 1px solid #334155; margin-top: 4rem; }}
    </style>
</head>
<body>
    <header>
        <h1>{NICHE_CONFIG['site_name']}</h1>
        <p class="subtitle">{NICHE_CONFIG['niche_focus']}</p>
    </header>

    <div class="container">
        <div class="ad-slot">[Google AdSense Leaderboard Banner]</div>
        <h2>Latest Live Breakdowns</h2>
        <div class="trend-grid">
"""
    for t in generated_trends:
        index_html += f"""
            <div class="trend-card">
                <span class="badge">🔥 {t['traffic']}</span>
                <a href="{t['slug']}.html">{t['title']}</a>
                <span style="color: var(--muted); font-size: 0.85rem;">Generated & verified automatically</span>
            </div>
"""
        
    index_html += f"""
        </div>
        <div class="ad-slot">[Google AdSense Multiplex Ad Unit]</div>
    </div>

    <footer>
        <p>&copy; {datetime.utcnow().year} {NICHE_CONFIG['site_name']}. Powered by Automated Pipelines.</p>
    </footer>
</body>
</html>
"""
    return index_html

def generate_sitemap(generated_trends):
    """Generates a clean XML sitemap to force fast search engine indexing."""
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += '  <url>\n'
    xml += '    <loc>https://yourdomain.com/index.html</loc>\n'
    xml += f'    <lastmod>{datetime.utcnow().strftime("%Y-%m-%d")}</lastmod>\n'
    xml += '    <changefreq>hourly</changefreq>\n'
    xml += '    <priority>1.0</priority>\n'
    xml += '  </url>\n'
    
    for t in generated_trends:
        xml += '  <url>\n'
        xml += f'    <loc>https://yourdomain.com/{t["slug"]}.html</loc>\n'
        xml += f'    <lastmod>{datetime.utcnow().strftime("%Y-%m-%d")}</lastmod>\n'
        xml += '    <changefreq>daily</changefreq>\n'
        xml += '    <priority>0.8</priority>\n'
        xml += '  </url>\n'
        
    xml += '</urlset>'
    return xml

def main():
    os.makedirs(NICHE_CONFIG['output_dir'], exist_ok=True)
    trends = fetch_google_trends()
    
    successfully_generated = []
    
    for trend in trends:
        print(f"Processing trend: {trend['title']}")
        ai_data = generate_ai_content(trend)
        
        if not ai_data:
            print(f"Skipped/Filtered out: {trend['title']}")
            continue
            
        page_html = build_page_html(trend, ai_data)
        file_path = os.path.join(NICHE_CONFIG['output_dir'], f"{trend['slug']}.html")
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(page_html)
            
        successfully_generated.append(trend)
        print(f"Successfully generated: {trend['slug']}.html")
        
    if successfully_generated:
        # Build index and sitemap
        index_html = update_index_page(successfully_generated)
        with open(os.path.join(NICHE_CONFIG['output_dir'], "index.html"), "w", encoding="utf-8") as f:
            f.write(index_html)
            
        sitemap_xml = generate_sitemap(successfully_generated)
        with open(os.path.join(NICHE_CONFIG['output_dir'], "sitemap.xml"), "w", encoding="utf-8") as f:
            f.write(sitemap_xml)
            
        print("Pipeline execution complete. All pages, index, and sitemap updated successfully!")
    else:
        print("No valid trends passed the strict filters during this run.")

if __name__ == "__main__":
    main()