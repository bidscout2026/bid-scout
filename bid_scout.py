"""
招标商机快报 - PythonAnywhere 云端版
自主搜索江西洗涤相关招标信息，通过邮件推送到 QQ 邮箱
每天由 PythonAnywhere Scheduled Task 触发运行
"""

import smtplib
import json
import os
import re
import urllib.request
import urllib.parse
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from html.parser import HTMLParser

# ========== 配置（硬编码，PythonAnywhere 免费版不支持自定义环境变量）==========
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
SENDER_EMAIL = "891329001@qq.com"
SENDER_AUTH_CODE = "imrvwqbyajigbfgg"
RECEIVER_EMAIL = "891329001@qq.com"

REGION = "江西"
KEYWORDS = ["洗涤", "布草洗涤", "织物洗涤", "洗涤服务"]
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_history.json")


# ========== 历史记录管理 ==========
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_sent": "", "sent_titles": []}


def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ========== 搜索：中国政府采购网 ==========
def search_ccgp(keyword, region="江西"):
    """通过中国政府采购网搜索接口获取招标信息"""
    results = []
    base_url = "http://search.ccgp.gov.cn/bxsearchgssearchapi/searchapi/search"
    
    # 搜索参数
    params = {
        "kw": keyword,
        "pageNo": "1",
        "pageSize": "20",
        "dbselect": "bid",  # 招标公告
        "bidSort": "0",
        "pinMu": "0",
        "pinMuSS": "0",
        "bidType": "1",
        "gpClass": "0",
        "startDate": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
        "endDate": datetime.now().strftime("%Y-%m-%d"),
    }

    try:
        url = base_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "http://search.ccgp.gov.cn/bxsearchgssearchapi/home.html"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits:
                item = hit.get("_source", {})
                title = item.get("title", "")
                # 只保留包含地区关键词和洗涤关键词的结果
                if region in title or keyword in title:
                    results.append({
                        "title": title.strip(),
                        "source": "中国政府采购网",
                        "link": item.get("url", ""),
                        "deadline": item.get("endDate", ""),
                        "budget": item.get("budget", ""),
                        "date": item.get("startDate", "")[:10] if item.get("startDate") else "",
                    })
    except Exception as e:
        print(f"  CCGP搜索 '{keyword}' 出错: {e}")

    return results


# ========== 搜索：江西公共资源交易网（备选，直接爬取容易失败）==========
def search_jiangxi_ggzy(keyword):
    """尝试通过江西公共资源交易网搜索（备选方案，可能被反爬拦截）"""
    results = []
    try:
        search_url = "http://ggzy.jiangxi.gov.cn/jyxx/searchResult.html"
        params = urllib.parse.urlencode({"keyword": keyword, "channelId": "002006004"})
        url = f"{search_url}?{params}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
            # 简单提取标题和链接
            titles = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>([^<]*洗涤[^<]*)</a>', html)
            for link, title in titles:
                results.append({
                    "title": title.strip(),
                    "source": "江西公共资源交易网",
                    "link": link,
                    "deadline": "",
                    "budget": "",
                    "date": "",
                })
    except Exception as e:
        print(f"  江西公共资源交易网搜索出错（可能被反爬拦截）: {e}")

    return results


# ========== 搜索：百度搜索（兜底方案，提取政府采购相关结果）==========
def search_baidu(keyword, region="江西"):
    """通过百度搜索获取招标信息（兜底方案）"""
    results = []
    query = f"{region} {keyword} 招标公告"
    url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}&rn=20"
    
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
            # 提取搜索结果
            pattern = r'<h3[^>]*class="c-title"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
            matches = re.findall(pattern, html, re.DOTALL)
            for link, title_html in matches:
                clean_title = re.sub(r'<[^>]+>', '', title_html).strip()
                if clean_title and (keyword in clean_title or "洗涤" in clean_title):
                    results.append({
                        "title": clean_title,
                        "source": "百度搜索",
                        "link": link,
                        "deadline": "",
                        "budget": "",
                        "date": "",
                    })
    except Exception as e:
        print(f"  百度搜索出错: {e}")

    return results


# ========== 去重合并 ==========
def merge_results(all_results):
    """去重合并搜索结果"""
    seen_titles = set()
    merged = []
    for item in all_results:
        # 简化标题用于去重（去掉空格和特殊字符）
        key = re.sub(r'[\s（）()【】\[\]]', '', item["title"])
        if key not in seen_titles:
            seen_titles.add(key)
            merged.append(item)
    return merged


# ========== 邮件格式 ==========
def format_email(active_results, new_count):
    today = datetime.now().strftime("%Y年%m月%d日")
    html = f"""<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1e293b;background:#f8fafc;padding:20px;margin:0}}
.container{{max-width:640px;margin:0 auto}}h1{{color:#2563eb;font-size:22px;border-bottom:2px solid #2563eb;padding-bottom:8px}}
.summary{{background:#dbeafe;border-radius:8px;padding:12px 16px;margin:16px 0;font-size:14px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px;margin-bottom:10px}}
.card-title{{font-size:15px;font-weight:600;margin-bottom:6px}}.card-meta{{font-size:13px;color:#64748b}}
.card-meta a{{color:#2563eb;text-decoration:none}}.tag{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}}
.tag-active{{background:#dcfce7;color:#166534}}.tag-new{{background:#fef3c7;color:#92400e}}.tag-all{{background:#f1f5f9;color:#64748b}}
.footer{{text-align:center;color:#94a3b8;font-size:12px;margin-top:24px;padding-top:12px;border-top:1px solid #e2e8f0}}
</style></head><body><div class="container"><h1>招标商机快报</h1>
<div class="summary">{today} | {REGION}洗涤相关<br>
<span class="tag tag-active">进行中 {len(active_results)} 条</span>
<span class="tag tag-new">新增 {new_count} 条</span></div>"""

    if active_results:
        html += "<h2>进行中的招标商机</h2>"
        for i, item in enumerate(active_results, 1):
            new_badge = ' <span class="tag tag-new">NEW</span>' if item.get("is_new") else ""
            date_info = f' | 截止 {item["deadline"]}' if item.get("deadline") else ""
            budget_info = f' | 预算 {item["budget"]}万' if item.get("budget") else ""
            link_html = f'<a href="{item["link"]}" target="_blank">查看详情</a>' if item.get("link") else ""
            source_info = item.get("source", "")
            html += f"""<div class="card">
<div class="card-title">{i}. {item["title"]}{new_badge}</div>
<div class="card-meta">{source_info}{date_info}{budget_info} | {link_html}</div></div>"""
    else:
        html += '<div class="card"><div class="card-title" style="color:#64748b;">今日暂无新增进行中的洗涤相关招标商机</div></div>'

    html += f"""<div class="footer">
由「招标商机快报」自动生成 | 数据来源：中国政府采购网、江西公共资源交易网<br>
运行环境：PythonAnywhere 云端 | 如需调整关键词或频率请修改脚本配置</div></div></body></html>"""
    return html


# ========== 发送邮件 ==========
def send_email(subject, html_content):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        server.login(SENDER_EMAIL, SENDER_AUTH_CODE)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        server.quit()
        print(f"邮件发送成功 -> {RECEIVER_EMAIL}")
        return True
    except Exception as e:
        print(f"邮件发送失败: {e}")
        return False


# ========== 主流程 ==========
def main():
    print(f"=== 招标商机快报（云端版）{datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    # 1. 多源搜索
    all_results = []
    print("\n[1] 搜索中国政府采购网...")
    for kw in KEYWORDS:
        results = search_ccgp(kw)
        all_results.extend(results)
        print(f"  关键词 '{kw}' 找到 {len(results)} 条")

    print("\n[2] 搜索江西公共资源交易网...")
    jx_results = search_jiangxi_ggzy("洗涤")
    all_results.extend(jx_results)
    print(f"  找到 {len(jx_results)} 条")

    # 如果以上两个来源都没有结果，尝试百度搜索
    if len(all_results) < 3:
        print("\n[3] 主源结果不足，尝试百度搜索兜底...")
        baidu_results = search_baidu("洗涤服务")
        all_results.extend(baidu_results)
        print(f"  百度找到 {len(baidu_results)} 条")

    # 2. 去重合并
    active_results = merge_results(all_results)
    print(f"\n去重后共 {len(active_results)} 条商机")

    # 3. 检查新增
    history = load_history()
    sent_titles = set(history.get("sent_titles", []))
    new_count = 0
    for item in active_results:
        if item["title"] not in sent_titles:
            item["is_new"] = True
            new_count += 1
            sent_titles.add(item["title"])

    # 4. 更新历史
    history["sent_titles"] = list(sent_titles)[-200:]
    history["last_sent"] = datetime.now().isoformat()
    save_history(history)

    # 5. 生成并发送邮件
    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"【招标商机快报】{REGION}洗涤招标-{today}"
    if new_count > 0:
        subject = f"【招标商机快报】{REGION}洗涤招标-{today} (新增{new_count}条新商机)"

    html = format_email(active_results, new_count)
    success = send_email(subject, html)

    # 6. 输出日志
    print(f"\n进行中: {len(active_results)} 条 | 新增: {new_count} 条")
    for i, item in enumerate(active_results, 1):
        badge = " [NEW]" if item.get("is_new") else ""
        print(f"  {i}. {item['title']}{badge}")

    if not success:
        output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_output.html")
        with open(output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"邮件内容已保存到 {output}")

    print(f"\n=== 完成 ===")


if __name__ == "__main__":
    main()
