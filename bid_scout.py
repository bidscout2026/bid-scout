"""
招标商机快报 - GitHub Actions 云端版
自主搜索江西洗涤相关招标信息，通过邮件推送到 QQ 邮箱
每天由 GitHub Actions 定时工作流触发运行
"""

import smtplib
import os
import re
import time
import json
import ssl
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

# ========== 配置 ==========
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "891329001@qq.com")
SENDER_AUTH_CODE = os.environ.get("SENDER_AUTH_CODE", "imrvwqbyajigbfgg")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "891329001@qq.com")

REGION = "江西"
# 搜索关键词（会在 CCGP 上逐个搜索）
KEYWORDS = ["洗涤", "布草洗涤", "织物洗涤", "洗涤服务"]
# 江西公共资源交易网关键词
JX_KEYWORDS = ["洗涤", "布草洗涤", "织物洗涤"]
# 公告类型排除（已结束/非招标类不推）
EXCLUDE_CATEGORIES = ["结果公示", "成交公示", "合同公示", "废标公告", "流标公告"]
# 地区关键词（严格筛选：只有命中这些关键词的才算江西）
REGION_KEYWORDS = ["江西", "南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶"]
# 只推送最近 N 天内发布的信息（避免陈年旧公告）
MAX_AGE_DAYS = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 全国公共资源交易平台（海外可达，作为江西交易网替代数据源）
GGZY_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.ggzy.gov.cn",
    "Referer": "https://www.ggzy.gov.cn/deal/dealList.html",
    "X-Pass-Token": "",
}

GGZY_API = "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList"
# 江西省份代码
GGZY_PROVINCE_JX = "360000"
# 近一月时间选项
GGZY_DEAL_TIME_MONTH = "04"
# 信息类型：0201=采购/资审公告（保留），0202=中标公告（剔除）
GGZY_KEEP_INFO_TYPES = ["0201"]


class LegacySSLAdapter(HTTPAdapter):
    """适配使用旧版 TLS/SSL 的国内政务网站（允许不安全重协商）"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except AttributeError:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except AttributeError:
            pass
        kwargs["ssl_context"] = ctx
        return super().proxy_manager_for(*args, **kwargs)


def search_ggzy(keyword, max_pages=2):
    """搜索全国公共资源交易平台（限定江西 + 近一月），海外 GitHub Actions 可达"""
    results = []
    seen_ids = set()
    for pg in range(1, max_pages + 1):
        form = {
            "DEAL_PROVINCE": GGZY_PROVINCE_JX,   # 江西
            "FINDTXT": keyword,                  # 关键词
            "DEAL_TIME": GGZY_DEAL_TIME_MONTH,   # 近一月
            "PAGENUMBER": str(pg),
        }
        try:
            resp = requests.post(GGZY_API, data=form, headers=GGZY_API_HEADERS, timeout=30)
            if resp.status_code != 200:
                print(f"  GGZY搜索 '{keyword}' 第{pg}页 HTTP {resp.status_code}")
                break
            data = resp.json()
            if data.get("code") != 200:
                print(f"  GGZY搜索 '{keyword}' 第{pg}页 code={data.get('code')} msg={data.get('message')}")
                break
            d = data.get("data", {})
            records = d.get("records", [])
            for rec in records:
                rid = rec.get("id", "")
                if rid and rid in seen_ids:
                    continue
                if rid:
                    seen_ids.add(rid)
                title = rec.get("title", "") or ""
                # 剥离平台前缀标签（如 ［江西省公共资源交易平台］），避免同一公告因前缀不同而重复
                title = re.sub(r'^\s*[［\[][^］\]]{2,20}[］\]]', '', title).strip()
                webdate = rec.get("publishTime", "") or ""
                link = rec.get("url", "") or ""
                if link and not link.startswith("http"):
                    link = "https://www.ggzy.gov.cn" + link
                info_type = rec.get("informationType", "") or ""
                info_type_text = rec.get("informationTypeText", "") or ""
                platform = rec.get("transactionSourcesPlatformText", "") or ""
                # 只保留采购/资审公告类型（0201），剔除中标/结果等
                if info_type not in GGZY_KEEP_INFO_TYPES:
                    continue
                results.append({
                    "title": title,
                    "source": "全国公共资源交易平台",
                    "link": link,
                    "deadline": "",
                    "budget": "",
                    "date": webdate[:10] if webdate else "",
                    "full_text": title,
                    "category": info_type_text,
                    "platform": platform,
                    "region_verified": True,  # 平台已按江西省份筛选，跳过地区关键词检查
                })
            total = d.get("total", 0)
            print(f"  关键词 '{keyword}' 第{pg}页: 返回 {len(records)} 条, 累计保留 {len(results)} 条, 总 {total} 条")
            if len(records) < 20:
                break
        except Exception as e:
            print(f"  GGZY搜索 '{keyword}' 第{pg}页出错: {e}")
            break
        time.sleep(0.5)
    return results


# ========== GGZY 详情页：提取截止时间 + 项目结束状态 ==========
DETAIL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.ggzy.gov.cn/deal/dealList.html",
}


def now_beijing():
    """当前北京时间（GitHub Actions 云端为 UTC，需 +8；本地为北京时间不受影响）"""
    return datetime.utcnow() + timedelta(hours=8) if os.environ.get("GITHUB_ACTIONS") == "true" else datetime.now()


# 详情页缓存（同一次运行内不重复抓取）
_detail_cache = {}
_aggr_cache = {}


def fetch_ggzy_detail(link):
    """抓取 GGZY b/ 详情页正文，返回（正文文本, 是否成功）。带内存缓存与重试。"""
    if link in _detail_cache:
        return _detail_cache[link]
    b_url = link.replace("/html/a/", "/html/b/")
    text = ""
    ok = False
    for attempt in range(2):
        try:
            resp = requests.get(b_url, headers=DETAIL_HEADERS, timeout=20)
            if resp.status_code == 200:
                resp.encoding = "utf-8"
                soup = BeautifulSoup(resp.text, "html.parser")
                dc = soup.select_one(".detail_content")
                if dc:
                    text = dc.get_text()
                    ok = len(text) > 100  # 正文太短视为未加载成功
                    if ok:
                        break
        except Exception:
            pass
        time.sleep(1)
    _detail_cache[link] = (text, ok)
    return text, ok


def check_project_finished(a_url):
    """检查 GGZY a/ 聚合页：项目是否已出结果/中标/成交/废标/流标公告。返回 (是否已结束, 结束公告标题)"""
    if a_url in _aggr_cache:
        return _aggr_cache[a_url]
    finished_kw = ["结果公告", "中标公告", "成交公告", "结果公示", "中标公示", "成交公示", "废标公告", "流标公告"]
    result = (False, "")
    try:
        resp = requests.get(a_url, headers=DETAIL_HEADERS, timeout=20)
        if resp.status_code == 200:
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            titles = [a.get_text(strip=True) for a in soup.select(".fully_list li a")]
            for t in titles:
                if any(k in t for k in finished_kw):
                    result = (True, t)
                    break
    except Exception:
        pass
    _aggr_cache[a_url] = result
    return result


def extract_deadline_from_detail(text):
    """从 GGZY 详情正文提取投标截止时间，返回统一格式字符串（如 '2026年11月11日 09:00'）"""
    if not text:
        return ""
    # 模式1：并于 2026年11月11日 09点00分 （北京时间）前递交投标文件
    m = re.search(r'于\s*(\d{4}年\d{1,2}月\d{1,2}日)\s*(\d{1,2}[:：点时]\d{2})[^。；]{0,30}前递交', text)
    if m:
        tm = m.group(2).replace("点", ":").replace("时", ":").replace("：", ":")
        return f"{m.group(1)} {tm}"
    # 模式2：截止时间：2026-11-11 09:00 / 截止日期：2026年11月11日
    m = re.search(r'截止[时间日期][：:]\s*(\d{4}-\d{2}-\d{2})(?:\s*(\d{1,2}:\d{2}))?', text)
    if m:
        return f"{m.group(1)} {m.group(2) or ''}".strip()
    m = re.search(r'截止[时间日期][：:]\s*(\d{4}年\d{1,2}月\d{1,2}日)(?:\s*(\d{1,2}[:：点时]\d{2}))?', text)
    if m:
        tm = (m.group(2) or "").replace("点", ":").replace("时", ":").replace("：", ":")
        return f"{m.group(1)} {tm}".strip()
    # 模式3：投标/递交/响应文件 ... 截止 ... 2026年11月11日09时
    m = re.search(r'(?:投标|递交|响应文件|报价)[^。；\n]{0,15}截止[^。；\n]{0,25}?(\d{4}年\d{1,2}月\d{1,2}日)[^。；\n]{0,15}?(\d{1,2}[:：点时]\d{2})?', text)
    if m:
        tm = (m.group(2) or "").replace("点", ":").replace("时", ":").replace("：", ":")
        return f"{m.group(1)} {tm}".strip()
    m = re.search(r'(?:投标|递交|响应文件|报价)[^。；\n]{0,15}截止[^。；\n]{0,25}?(\d{4}-\d{2}-\d{2})[^。；\n]{0,10}?(\d{1,2}:\d{2})?', text)
    if m:
        return f"{m.group(1)} {m.group(2) or ''}".strip()
    return ""


def parse_cn_datetime(text):
    """解析 '2026年11月11日 09:00' 或 '2026-11-11 09:00' 为 datetime，失败返回 None"""
    if not text:
        return None
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})?:?(\d{2})?', text)
    if m:
        try:
            hh = int(m.group(4)) if m.group(4) else 0
            mm = int(m.group(5)) if m.group(5) else 0
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), hh, mm)
        except ValueError:
            return None
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})(?:\s*(\d{1,2}):(\d{2}))?', text)
    if m:
        try:
            hh = int(m.group(4)) if m.group(4) else 0
            mm = int(m.group(5)) if m.group(5) else 0
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), hh, mm)
        except ValueError:
            return None
    return None


def enrich_ggzy_results(results):
    """对 GGZY 结果逐条补全截止日期/预算，并剔除：已结束项目、截止已过的项目。
    返回（有效结果列表, 被剔除列表）"""
    valid = []
    dropped = []
    now_bj = now_beijing()
    print(f"\n[3] 核查 {len(results)} 条 GGZY 公告的截止时间与项目状态...")
    for i, item in enumerate(results, 1):
        title = item.get("title", "")
        link = item.get("link", "")
        short = title[:38]
        # 1) 项目是否已结束（聚合页有结果/中标/废标/流标公告）
        finished, fin_title = check_project_finished(link)
        if finished:
            dropped.append((item, f"项目已出结束公告: {fin_title[:30]}"))
            print(f"  [{i}] ✂ 剔除（已结束） {short}")
            continue
        # 2) 抓详情页补全截止时间/预算
        text, ok = fetch_ggzy_detail(link)
        deadline = extract_deadline_from_detail(text)
        budget = extract_budget(text)
        if deadline:
            item["deadline"] = deadline
        if budget:
            item["budget"] = budget
        # 3) 截止时间已过 → 剔除
        dl_dt = parse_cn_datetime(deadline)
        if dl_dt and dl_dt < now_bj:
            dropped.append((item, f"截止已过: {deadline}"))
            print(f"  [{i}] ✂ 剔除（截止已过 {deadline}） {short}")
            continue
        # 4) 详情抓不到（正文为空，可能限流）：按发布日期兜底
        if not deadline:
            date_str = item.get("date", "")
            if date_str:
                try:
                    pub_date = datetime.strptime(date_str, "%Y-%m-%d")
                    age_days = (now_bj - pub_date).days
                    if age_days > 20:
                        dropped.append((item, f"发布超20天且无截止信息"))
                        print(f"  [{i}] ✂ 剔除（发布{age_days}天且无截止信息） {short}")
                        continue
                except ValueError:
                    pass
            print(f"  [{i}] ? 无截止信息（保留，发布{date_str}） {short}")
        else:
            print(f"  [{i}] ✓ 截止 {deadline} {short}")
        valid.append(item)
        time.sleep(0.4)  # 温和限速
    print(f"  核查完成: 有效 {len(valid)} 条, 剔除 {len(dropped)} 条")
    return valid, dropped



# ========== 搜索：中国政府采购网 ==========
def search_ccgp(keyword, max_retries=3):
    """通过中国政府采购网搜索页面获取招标信息（带重试）"""
    results = []
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
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            # CCGP 搜索结果列表选择器
            items = soup.select("ul.vT-srch-result-list-bid li")

            for item in items:
                a_tag = item.find("a")
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                link = a_tag.get("href", "")
                if link and not link.startswith("http"):
                    link = "http://www.ccgp.gov.cn" + link

                # 从 URL 中提取发布日期
                date = ""
                date_match = re.search(r"t(\d{4})(\d{2})(\d{2})", link)
                if date_match:
                    date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"

                full_text = item.get_text()
                deadline = extract_deadline(full_text)
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
            break  # 成功就跳出重试
        except Exception as e:
            print(f"  CCGP搜索 '{keyword}' 第{attempt}次出错: {e}")
            if attempt < max_retries:
                time.sleep(5)
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
    """判断是否与江西相关（全国平台已按省份筛选，直接通过）"""
    if item.get("region_verified"):
        return True
    text = item.get("title", "") + item.get("full_text", "")
    for kw in REGION_KEYWORDS:
        if kw in text:
            return True
    return False


def is_active_bid(item):
    """判断是否为进行中的招标公告（剔除结果/废标/合同/更正等）"""
    cat = item.get("category", "")
    if cat:
        for exc in EXCLUDE_CATEGORIES:
            if exc in cat:
                return False
    # 标题含明显结束/变更标记也剔除
    title = item.get("title", "")
    for exc in ["废标", "流标", "结果公告", "结果公示", "中标公示", "成交结果", "更正公告", "变更公告", "澄清公告", "终止公告", "暂停公告"]:
        if exc in title:
            return False
    # 截止日期已过的剔除（有截止日期才校验）
    deadline = item.get("deadline", "")
    if deadline:
        dl = parse_deadline(deadline)
        if dl and dl < now_beijing():
            return False
    return True


def parse_deadline(text):
    """解析截止时间字符串为 datetime（支持常见中文/数字格式）"""
    patterns = [
        r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*[点:：]\s*(\d{1,2})',
        r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
        r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})',
        r'(\d{4})-(\d{2})-(\d{2})',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            g = m.groups()
            try:
                if len(g) == 5:
                    return datetime(int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]))
                elif len(g) == 3:
                    return datetime(int(g[0]), int(g[1]), int(g[2]))
            except ValueError:
                return None
    return None


def is_fresh(item, max_age_days=MAX_AGE_DAYS):
    """判断发布日期是否在最近 N 天内（无日期的默认保留）"""
    date_str = item.get("date", "")
    if not date_str:
        return True
    try:
        pub_date = datetime.strptime(date_str, "%Y-%m-%d")
        age_days = (now_beijing() - pub_date).days
        return age_days <= max_age_days
    except ValueError:
        return True


# ========== 去重合并 ==========
def normalize_title(title):
    """标题规范化：去空格/标点（含全角括号），便于比对"""
    return re.sub(r'[\s（）()【】\[\]［］：:，,。.、-]', '', title or "")


def extract_project_no(title):
    """从标题提取项目编号，兼容两种格式：
    JXTC2026040477C1（连续型）与 XYRJSGC-2026-001-01（连字符型）"""
    m = re.search(r'[A-Za-z]{2,}[0-9A-Za-z\-]{4,}', title or "")
    return m.group(0).upper() if m else ""


def project_base_no(title):
    """项目编号归一化：去掉标段/次序后缀，识别同一项目的不同公告。
    例：XYRJSGC-2026-001-01 → XYRJSGC-2026-001；JXTC2026040477C1 → JXTC2026040477"""
    no = extract_project_no(title)
    if not no:
        return ""
    # 1) 去掉尾部 -NN 纯数字段（-01 / -1 / -2 等，代表第N次/标段）
    no = re.sub(r'-\d{1,2}$', '', no)
    # 2) 去掉尾部字母+短数字后缀（C1 / C2 / B2 等标段标记）
    m = re.match(r'^(.*?)(?:[A-Za-z]\d{1,2})$', no)
    if m and len(m.group(1)) >= 8:
        no = m.group(1)
    return no.upper()


def extract_buyer(title):
    """从标题提取采购人（医院/学校/酒店等机构名，优先取“关于”之后的真正采购人）"""
    t = title or ""
    m = re.search(r'关于([^，,。；;]{2,20}?(?:医院|卫生院|保健院|学校|大学|学院|中学|小学|幼儿园|酒店|宾馆|公司|中心|管理局|办事处|政府|集团|疗养院|护理院))', t)
    if m:
        return m.group(1)
    m = re.search(r'(?:^|］|\])((?:江西)?[^，,。；;]{2,20}?(?:医院|卫生院|保健院|学校|大学|学院|中学|小学|幼儿园|酒店|宾馆|中心|管理局|办事处|政府|集团|疗养院|护理院))', t)
    return m.group(1) if m else ""


def merge_results(all_results):
    """三层去重：完全标题 → 基础项目编号 → 采购人+核心词。
    先按发布日期降序、有截止时间优先排序，保证重复组里“最新最全”的公告先入选、旧公告被丢弃。
    被丢弃的重复条目若带有保留条目缺失的截止时间/预算，补回保留条目（信息择优）。"""
    ordered = sorted(all_results, key=lambda r: (r.get("date") or "", bool(r.get("deadline"))), reverse=True)
    all_results = ordered
    seen_keys = {}
    seen_base_nos = {}
    seen_buyers = {}
    merged = []
    for item in all_results:
        key = normalize_title(item["title"])
        base_no = project_base_no(item["title"])
        # 核心词：去掉机构名后剩余的业务词（洗涤/布草/织物等）
        title_core = re.sub(r'[^\u4e00-\u9fa5]', '', item["title"])
        for w in ["竞争性磋商", "公开招标", "电子化", "公告", "采购项目", "服务项目", "第二次", "第三次", "第三次", "关于", "有限公司", "招标", "咨询", "建设工程", "机电设备", "管理", "代理", "采购"]:
            title_core = title_core.replace(w, "")
        buyer = extract_buyer(item["title"])
        buyer_key = (buyer, title_core[:12]) if buyer and len(title_core) >= 4 else None
        dup_idx = None
        if key in seen_keys:
            dup_idx = seen_keys[key]
        elif base_no and base_no in seen_base_nos:
            dup_idx = seen_base_nos[base_no]
        elif buyer_key and buyer_key in seen_buyers:
            dup_idx = seen_buyers[buyer_key]
        if dup_idx is not None:
            kept = merged[dup_idx]
            for field in ("deadline", "budget"):
                if not kept.get(field) and item.get(field):
                    kept[field] = item[field]
            continue
        idx = len(merged)
        merged.append(item)
        seen_keys[key] = idx
        if base_no:
            seen_base_nos[base_no] = idx
        if buyer_key:
            seen_buyers[buyer_key] = idx
    return merged


# ========== 邮件格式 ==========
def format_email(active_results, new_count):
    today = now_beijing().strftime("%Y年%m月%d日")
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
        html += '<div class="card"><div class="card-title" style="color:#64748b;">今日暂无新增的江西洗涤相关招标商机</div><div class="card-meta">已按「仅江西、最近30天」严格筛选，无匹配结果时不推送外地信息</div></div>'
    html += f"""<div class="footer">
由「招标商机快报」自动生成 | 数据来源：中国政府采购网 + 全国公共资源交易平台<br>
筛选条件：仅江西地区 + 最近{MAX_AGE_DAYS}天 | 运行环境：GitHub Actions 云端</div></div></body></html>"""
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
    print(f"=== 招标商机快报（GitHub Actions 云端版）{now_beijing().strftime('%Y-%m-%d %H:%M')} ===")

    all_results = []

    # 第一步：搜索中国政府采购网
    print("\n[1] 搜索中国政府采购网...")
    for kw in KEYWORDS:
        results = search_ccgp(kw)
        all_results.extend(results)
        print(f"  关键词 '{kw}' 找到 {len(results)} 条")

    # 第二步：搜索全国公共资源交易平台（江西+洗涤）
    print("\n[2] 搜索全国公共资源交易平台（江西）...")
    jx_count = 0
    for kw in JX_KEYWORDS:
        results = search_ggzy(kw)
        all_results.extend(results)
        jx_count += len(results)
        print(f"  关键词 '{kw}' 找到 {len(results)} 条")
    print(f"  全国平台江西合计 {jx_count} 条")

    # 去重
    all_results = merge_results(all_results)
    print(f"\n  去重后共 {len(all_results)} 条全国结果")

    # 筛选江西相关 + 最近30天内发布 + 进行中招标
    jiangxi_results = [r for r in all_results if is_region_related(r) and is_fresh(r) and is_active_bid(r)]
    print(f"  筛选江西相关且{MAX_AGE_DAYS}天内进行中: {len(jiangxi_results)} 条")

    # GGZY 来源核查：逐条抓详情页，补全截止日期/预算，剔除已结束项目和截止已过项目
    ggzy_items = [r for r in jiangxi_results if r.get("source") == "全国公共资源交易平台"]
    if ggzy_items:
        ggzy_valid, ggzy_dropped = enrich_ggzy_results(ggzy_items)
        ggzy_links = {id(r) for r in ggzy_valid}
        other_items = [r for r in jiangxi_results if r.get("source") != "全国公共资源交易平台"]
        jiangxi_results = other_items + ggzy_valid
        print(f"  GGZY 核查后剩余 {len(ggzy_valid)} 条（剔除 {len(ggzy_dropped)} 条过期/已结束）")
    for r in jiangxi_results:
        print(f"    - {r['title'][:50]}")

    # 严格模式：只推江西，没有就发空报（不发外地信息）
    active_results = jiangxi_results

    # 所有结果都算新增（GitHub Actions 无状态）
    new_count = len(active_results)
    for item in active_results:
        item["is_new"] = True

    # 生成并发送邮件
    today = now_beijing().strftime("%Y-%m-%d")
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
