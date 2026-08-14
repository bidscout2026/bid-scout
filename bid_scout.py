"""
招标商机快报 - GitHub Actions 云端版
自主搜索江西洗涤相关招标信息，通过邮件推送到 QQ 邮箱
每天由 GitHub Actions 定时工作流触发运行
"""

import smtplib
import os
import re
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from bs4 import BeautifulSoup

# ========== 配置 ==========
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "891329001@qq.com")
SENDER_AUTH_CODE = os.environ.get("SENDER_AUTH_CODE", "imrvwqbyajigbfgg")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "891329001@qq.com")

REGION = "江西"
# 搜索关键词（会在 CCGP 上逐个搜索）
KEYWORDS = ["洗涤", "布草洗涤", "织物洗涤", "洗涤服务"]
# 地区关键词（用于从全国结果中筛选江西相关）
REGION_KEYWORDS = ["江西", "南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ========== 搜索：中国政府采购网 ==========
def search_ccgp(keyword):
    """通过中国政府采购网搜索页面获取招标信息"""
    results = []
    try:
        url = "http://search.ccgp.gov.cn/bxsearch"
        params = {
            "searchtype": "1",
            "kw": keyword,
            "pageNo": "1",
            "pageSize": "20",
            "bidSort": "0",
            "pinMu": "0",
            "pinMuSS": "0",
            "bidType": "1",
            "gpClass": "0",
        }
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # CCGP 搜索结果列表选择器
        items = soup.select("ul.vT-srch-result-list-bid li")

        for item in items:
            # 标题在 <a> 标签中
            a_tag = item.find("a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            link = a_tag.get("href", "")
            if link and not link.startswith("http"):
                link = "http://www.ccgp.gov.cn" + link

            # 从 URL 中提取发布日期（如 t20260814_27133140.htm）
            date = ""
            date_match = re.search(r"t(\d{4})(\d{2})(\d{2})", link)
            if date_match:
                date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"

            # 从摘要中提取截止日期
            full_text = item.get_text()
            deadline = extract_deadline(full_text)

            # 从摘要中提取预算金额
            budget = extract_budget(full_text)

            if title:
                results.append({
                    "title": title,
                    "source": "中国政府采购网",
                    "link": link,
                    "deadline": deadline,
                    "budget": budget,
                    "date": date,
                    "full_text": full_text[:200],
                })
    except Exception as e:
        print(f"  CCGP搜索 '{keyword}' 出错: {e}")
    return results


def extract_deadline(text):
    """从文本中提取投标截止日期"""
    # 匹配 "2026年09月08日10:00" 或 "2026年09月08日 10:00" 或 "2026年09月08日"
    patterns = [
        r'(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})',
        r'(\d{4}年\d{1,2}月\d{1,2}日)',
        r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})',
        r'(\d{4}-\d{2}-\d{2})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def extract_budget(text):
    """从文本中提取预算金额"""
    # 匹配 "预算金额：XXX万元" 或 "采购预算：XXX万元" 或 "XXX万元"
    patterns = [
        r'预算[金额：:\s]*([\d.]+)\s*万元',
        r'采购预算[：:\s]*([\d.]+)\s*万元',
        r'预算[：:\s]*([\d.]+)\s*万',
        r'([\d.]+)\s*万元',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def is_region_related(item):
    """判断是否与江西相关"""
    text = item.get("title", "") + item.get("full_text", "")
    for kw in REGION_KEYWORDS:
        if kw in text:
            return True
    return False


# ========== 搜索：百度搜索（兜底方案）==========
def search_baidu(keyword):
    """通过百度搜索获取招标信息（兜底）"""
    results = []
    query = f"江西 {keyword} 招标公告 2026"
    try:
        url = f"https://www.baidu.com/s?wd={requests.utils.quote(query)}&rn=20"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        # 百度搜索结果可能在多种容器中
        items = soup.select(".result") or soup.select("div.c-container") or soup.select("[tpl]")

        for item in items:
            # 标题在 h3 a 中
            h3 = item.select_one("h3 a")
            if not h3:
                # 尝试其他方式
                a = item.find("a")
                if not a:
                    continue
                h3 = a
            title = h3.get_text(strip=True)
            link = h3.get("href", "")
            if title and ("洗涤" in title or keyword in title):
                results.append({
                    "title": title,
                    "source": "百度搜索",
                    "link": link,
                    "deadline": "",
                    "budget": "",
                    "date": "",
                    "full_text": "",
                })
    except Exception as e:
        print(f"  百度搜索出错: {e}")
    return results


# ========== 去重合并 ==========
def merge_results(all_results):
    seen_keys = set()
    merged = []
    for item in all_results:
        # 用标题去重（去除空格和标点）
        key = re.sub(r'[\s（）()【】\[\]【】]', '', item["title"])
        if key not in seen_keys:
            seen_keys.add(key)
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
.tag-active{{background:#dcfce7;color:#166534}}.tag-new{{background:#fef3c7;color:#92400e}}
.card-deadline{{color:#dc2626;font-weight:600;font-size:13px}}
.footer{{text-align:center;color:#94a3b8;font-size:12px;margin-top:24px;padding-top:12px;border-top:1px solid #e2e8f0}}
</style></head><body><div class="container"><h1>招标商机快报</h1>
<div class="summary">{today} | {REGION}洗涤相关<br>
<span class="tag tag-active">进行中 {len(active_results)} 条</span>
<span class="tag tag-new">新增 {new_count} 条</span></div>"""
    if active_results:
        html += "<h2>进行中的招标商机</h2>"
        for i, item in enumerate(active_results, 1):
            new_badge = ' <span class="tag tag-new">NEW</span>' if item.get("is_new") else ""
            deadline_html = f'<div class="card-deadline">截止时间：{item["deadline"]}</div>' if item.get("deadline") else ""
            budget_info = f' | 预算 {item["budget"]}万' if item.get("budget") else ""
            date_info = f' | 发布 {item["date"]}' if item.get("date") else ""
            link_html = f'<a href="{item["link"]}" target="_blank">查看详情</a>' if item.get("link") else ""
            source_info = item.get("source", "")
            html += f"""<div class="card">
<div class="card-title">{i}. {item["title"]}{new_badge}</div>
{deadline_html}
<div class="card-meta">{source_info}{budget_info}{date_info} | {link_html}</div></div>"""
    else:
        html += '<div class="card"><div class="card-title" style="color:#64748b;">今日暂无新增进行中的洗涤相关招标商机</div></div>'
    html += f"""<div class="footer">
由「招标商机快报」自动生成 | 数据来源：中国政府采购网<br>
运行环境：GitHub Actions 云端 | 如需调整关键词或频率请修改配置</div></div></body></html>"""
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
    print(f"=== 招标商机快报（GitHub Actions 云端版）{datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    all_results = []

    # 第一步：搜索中国政府采购网
    print("\n[1] 搜索中国政府采购网...")
    for kw in KEYWORDS:
        results = search_ccgp(kw)
        all_results.extend(results)
        print(f"  关键词 '{kw}' 找到 {len(results)} 条")

    # 去重
    all_results = merge_results(all_results)
    print(f"\n  去重后共 {len(all_results)} 条全国结果")

    # 筛选江西相关
    jiangxi_results = [r for r in all_results if is_region_related(r)]
    print(f"  筛选江西相关: {len(jiangxi_results)} 条")
    for r in jiangxi_results:
        print(f"    - {r['title'][:50]}")

    # 如果 CCGP 搜索不到江西结果，保留全部结果（避免空邮件）
    active_results = jiangxi_results if jiangxi_results else all_results[:5]

    # 如果还是空，使用百度兜底
    if not active_results:
        print("\n[2] CCGP 无结果，使用百度搜索兜底...")
        for kw in KEYWORDS[:2]:
            baidu_results = search_baidu(kw)
            active_results.extend(baidu_results)
            print(f"  百度关键词 '{kw}' 找到 {len(baidu_results)} 条")

    # 所有结果都算新增（GitHub Actions 无状态）
    new_count = len(active_results)
    for item in active_results:
        item["is_new"] = True

    # 生成并发送邮件
    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"【招标商机快报】{REGION}洗涤招标-{today}"
    if new_count > 0:
        subject = f"【招标商机快报】{REGION}洗涤招标-{today} (新增{new_count}条新商机)"

    html = format_email(active_results, new_count)
    success = send_email(subject, html)

    print(f"\n进行中: {len(active_results)} 条 | 新增: {new_count} 条")
    for i, item in enumerate(active_results, 1):
        badge = " [NEW]" if item.get("is_new") else ""
        deadline_str = f" (截止: {item['deadline']})" if item.get("deadline") else ""
        print(f"  {i}. {item['title'][:60]}{badge}{deadline_str}")

    if not success:
        output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_output.html")
        with open(output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"邮件内容已保存到 {output}")

    print(f"\n=== 完成 ===")


if __name__ == "__main__":
    main()
