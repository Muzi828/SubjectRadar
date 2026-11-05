from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "SafeLens"
APP_TITLE = "SafeLens · 安全科学论文雷达"
VERSION = "1.1.0"

ROOT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DATA_DIR = Path(os.environ.get("SAFELENS_DATA_DIR", ROOT_DIR / "data"))
LOG_DIR = Path(os.environ.get("SAFELENS_LOG_DIR", ROOT_DIR / "logs"))
DB_PATH = DATA_DIR / "safelens.db"
LOCK_PATH = DATA_DIR / "sync.lock"
PDF_DIR = Path(os.environ.get("SAFELENS_PDF_DIR", DATA_DIR / "pdfs"))

HOST = os.environ.get("SAFELENS_HOST", "0.0.0.0")
PORT = int(os.environ.get("SAFELENS_PORT", "8765"))
TIMEZONE_LABEL = "Asia/Shanghai"
TASK_NAME = "SafetyScienceMonitor_Daily0800"

USER_AGENT = (
    "SafeLens/1.0 (academic literature monitor; local desktop app; "
    "mailto:safelens.local@example.com)"
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 首次建库时每本期刊最多收录的条目数；之后每次检查也会保留最近这么多条。
FEED_LIMIT = 60
BOOTSTRAP_LIMIT = 60

# 期刊预设：只挑选官网 RSS 可稳定读取的核心期刊。
JOURNALS = [
    {
        "slug": "safety-science",
        "name": "Safety Science",
        "short_name": "Safety Sci.",
        "publisher": "Elsevier",
        "issn": "0925-7535",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/safety-science",
        "rss_url": "https://rss.sciencedirect.com/publication/science/09257535",
        "feed_type": "elsevier",
        "crossref_issn": "0925-7535",
        "color": "#C35A2E",
        "sort_order": 10,
    },
    {
        "slug": "reliability-engineering-system-safety",
        "name": "Reliability Engineering & System Safety",
        "short_name": "RESS",
        "publisher": "Elsevier",
        "issn": "0951-8320",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/reliability-engineering-and-system-safety",
        "rss_url": "https://rss.sciencedirect.com/publication/science/09518320",
        "feed_type": "elsevier",
        "crossref_issn": "0951-8320",
        "color": "#256C7A",
        "sort_order": 20,
    },
    {
        "slug": "journal-of-safety-research",
        "name": "Journal of Safety Research",
        "short_name": "J. Saf. Res.",
        "publisher": "Elsevier",
        "issn": "0022-4375",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/journal-of-safety-research",
        "rss_url": "https://rss.sciencedirect.com/publication/science/00224375",
        "feed_type": "elsevier",
        "crossref_issn": "0022-4375",
        "color": "#8A6A2F",
        "sort_order": 30,
    },
    {
        "slug": "accident-analysis-and-prevention",
        "name": "Accident Analysis & Prevention",
        "short_name": "Accid. Anal. Prev.",
        "publisher": "Elsevier",
        "issn": "0001-4575",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/accident-analysis-and-prevention",
        "rss_url": "https://rss.sciencedirect.com/publication/science/00014575",
        "feed_type": "elsevier",
        "crossref_issn": "0001-4575",
        "color": "#3F5F8A",
        "sort_order": 40,
    },
    {
        "slug": "process-safety-environmental-protection",
        "name": "Process Safety and Environmental Protection",
        "short_name": "Process Saf. Environ.",
        "publisher": "Elsevier",
        "issn": "0957-5820",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/process-safety-and-environmental-protection",
        "rss_url": "https://rss.sciencedirect.com/publication/science/09575820",
        "feed_type": "elsevier",
        "crossref_issn": "0957-5820",
        "color": "#8A4B57",
        "sort_order": 50,
    },
    {
        "slug": "fire-safety-journal",
        "name": "Fire Safety Journal",
        "short_name": "Fire Saf. J.",
        "publisher": "Elsevier",
        "issn": "0379-7112",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/fire-safety-journal",
        "rss_url": "https://rss.sciencedirect.com/publication/science/03797112",
        "feed_type": "elsevier",
        "crossref_issn": "0379-7112",
        "color": "#B24B2A",
        "sort_order": 60,
    },
    {
        "slug": "journal-of-loss-prevention",
        "name": "Journal of Loss Prevention in the Process Industries",
        "short_name": "J. Loss Prev. Process Ind.",
        "publisher": "Elsevier",
        "issn": "0950-4230",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/journal-of-loss-prevention-in-the-process-industries",
        "rss_url": "https://rss.sciencedirect.com/publication/science/09504230",
        "feed_type": "elsevier",
        "crossref_issn": "0950-4230",
        "color": "#5A6B3B",
        "sort_order": 70,
    },
    {
        "slug": "international-journal-disaster-risk-reduction",
        "name": "International Journal of Disaster Risk Reduction",
        "short_name": "IJDRR",
        "publisher": "Elsevier",
        "issn": "2212-4209",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/international-journal-of-disaster-risk-reduction",
        "rss_url": "https://rss.sciencedirect.com/publication/science/22124209",
        "feed_type": "elsevier",
        "crossref_issn": "2212-4209",
        "color": "#4B6E8A",
        "sort_order": 80,
    },
    {
        "slug": "safety-and-health-at-work",
        "name": "Safety and Health at Work",
        "short_name": "Saf. Health Work",
        "publisher": "Elsevier",
        "issn": "2093-7911",
        "eissn": "",
        "homepage_url": "https://www.sciencedirect.com/journal/safety-and-health-at-work",
        "rss_url": "https://rss.sciencedirect.com/publication/science/20937911",
        "feed_type": "elsevier",
        "crossref_issn": "2093-7911",
        "color": "#5E7D5A",
        "sort_order": 90,
    },
    {
        "slug": "fire-technology",
        "name": "Fire Technology",
        "short_name": "Fire Technol.",
        "publisher": "Springer",
        "issn": "0015-2684",
        "eissn": "1572-8092",
        "homepage_url": "https://link.springer.com/journal/10694",
        "rss_url": "https://link.springer.com/search.rss?facet-journal-id=10694",
        "feed_type": "springer",
        "crossref_issn": "0015-2684",
        "color": "#9A5B2A",
        "sort_order": 100,
    },
    {
        "slug": "risk-analysis",
        "name": "Risk Analysis",
        "short_name": "Risk Anal.",
        "publisher": "Wiley",
        "issn": "0272-4332",
        "eissn": "1539-6924",
        "homepage_url": "https://onlinelibrary.wiley.com/journal/15396924",
        "rss_url": "https://onlinelibrary.wiley.com/feed/15396924/most-recent",
        "feed_type": "wiley",
        "crossref_issn": "0272-4332",
        "color": "#6A4C93",
        "sort_order": 110,
    },
    {
        "slug": "occupational-environmental-medicine",
        "name": "Occupational and Environmental Medicine",
        "short_name": "Occup. Environ. Med.",
        "publisher": "BMJ",
        "issn": "1351-0711",
        "eissn": "1470-7926",
        "homepage_url": "https://oem.bmj.com/",
        "rss_url": "https://oem.bmj.com/rss/current.xml",
        "feed_type": "bmj",
        "crossref_issn": "1351-0711",
        "color": "#3C6E71",
        "sort_order": 120,
    },
    {
        "slug": "china-safety-science-journal",
        "name": "中国安全科学学报",
        "short_name": "中国安全科学学报",
        "publisher": "中国职业安全健康协会",
        "issn": "1003-3033",
        "eissn": "",
        "homepage_url": "http://www.cssjj.com.cn/CN/1003-3033/home.shtml",
        "rss_url": "http://www.cssjj.com.cn/CN/1003-3033/current.shtml",
        "feed_type": "cssjj",
        "crossref_issn": "",
        "color": "#B23A2E",
        "sort_order": 15,
    },
    {
        "slug": "china-safety-science-technology",
        "name": "中国安全生产科学技术",
        "short_name": "中国安全生产科学技术",
        "publisher": "中国安全生产科学研究院",
        "issn": "1673-193X",
        "eissn": "",
        "homepage_url": "http://aqsckj.paperopen.com/",
        "rss_url": "http://aqsckj.paperopen.com/api/api/Web/OAList?IssueId=-1&PageSize=40&PageIndex=1",
        "feed_type": "aqsckj",
        "crossref_issn": "",
        "color": "#8E4A2F",
        "sort_order": 25,
    },
    {
        "slug": "journal-safety-and-environment",
        "name": "安全与环境学报",
        "short_name": "安全与环境学报",
        "publisher": "北京理工大学",
        "issn": "1009-6094",
        "eissn": "",
        "homepage_url": "https://castjournals.cast.org.cn/joweb/aqyhjxb/CN/home",
        "rss_url": "https://castjournals.cast.org.cn/joweb/aqyhjxb/CN/home",
        "feed_type": "cast",
        "crossref_issn": "",
        "color": "#A8483C",
        "sort_order": 17,
    },
    {
        "slug": "journal-command-and-control",
        "name": "指挥与控制学报",
        "short_name": "指挥与控制学报",
        "publisher": "中国指挥与控制学会",
        "issn": "2096-0204",
        "eissn": "",
        "homepage_url": "https://www.jc2.org.cn/",
        "rss_url": "https://www.jc2.org.cn/rss/current.xml",
        "feed_type": "jc2",
        "crossref_issn": "2096-0204",
        "color": "#4F5D9E",
        "sort_order": 18,
    },
    {
        "slug": "safety-and-environmental-engineering",
        "name": "安全与环境工程",
        "short_name": "安全与环境工程",
        "publisher": "中国地质大学（武汉）",
        "issn": "1671-1556",
        "eissn": "",
        "homepage_url": "https://ktaq.cbpt.cnki.net/portal",
        "rss_url": "https://ktaq.cbpt.cnki.net/portal/journal/portal/client/paper_list/type_benqi",
        "feed_type": "cnki",
        "crossref_issn": "",
        "color": "#4E6B58",
        "sort_order": 27,
    },
]

TOPIC_RULES = [
    ("事故与伤害", r"事故|事故致因|事故分析|事故预防|事故调查|伤害|伤亡|死亡|坠落|坍塌|中毒|淹溺|触电|机械伤害|accident|injury|fatal|casualty|fall from height|collapse"),
    ("安全管理", r"安全管理|安全生产|安全监管|安全管理体系|双重预防|隐患排查|风险分级管控|安全生产责任制|安全投入|安全绩效|safety management|safety regulation|inspection|compliance|safety program"),
    ("安全文化", r"安全文化|安全氛围|安全意识|安全态度|安全行为|安全领导|safety culture|safety climate|safety behavior|safety leadership|just culture"),
    ("地质灾害与防灾", r"滑坡|泥石流|崩塌|地质灾害|地震|洪水|内涝|山洪|地面沉降|灾害风险|防灾|减灾|landside|landslide|debris flow|earthquake|flood|geohazard"),
    ("风险评估与管控", r"风险评估|风险评价|风险分析|风险管控|风险识别|风险分级|风险感知|风险控制|risk assessment|risk analysis|risk management|risk perception|risk control|bayesian network"),
    ("应急与救援", r"应急管理|应急救援|应急预案|应急响应|应急演练|应急决策|救援|消防应急|emergency management|emergency response|rescue|contingency|preparedness"),
    ("火灾与消防", r"火灾|消防|灭火|烟气|火蔓延|轰燃|火灾风险|森林火|建筑火灾|燃爆|爆炸|爆轰|\bfire\b|flame|smoke|suppression|ignition|explosion|blast|deflagration"),
    ("职业健康", r"职业健康|职业病|职业暴露|职业危害|粉尘|噪声|高温|振动|职业紧张|occupational health|occupational exposure|occupational disease|dust|noise|heat stress"),
    ("矿山安全", r"煤矿|矿山|瓦斯|冲击地压|顶板|透水|尾矿库|采空区|mining|mine\b|coal mine|rockburst|gas explosion|tailings"),
    ("化工与过程安全", r"化工|危险化学品|危化品|泄漏|工艺安全|反应失控|储罐|压力容器|管道|过程安全|合成气|燃气|氢气|粉尘爆炸|chemical|process safety|hazardous material|leak|runaway reaction|storage tank|syngas|hydrogen"),
    ("建筑施工安全", r"建筑施工|施工现场|脚手架|基坑|模板支架|塔吊|高处作业|建筑工人|construction|scaffold|excavation|formwork"),
    ("交通安全", r"道路交通|交通事故|驾驶|车辆|行人|骑行者|电动自行车|轨道交通|traffic|\broad\b|driver|driving|vehicle|crash|collision|pedestrian|cyclist|micromobility"),
    ("公共安全与城市安全", r"公共安全|城市安全|人员密集|大型活动|公共场所|社区安全|public safety|urban safety|mass gathering|public place"),
    ("安全智能化", r"人工智能|机器学习|深度学习|神经网络|大数据|数字孪生|物联网|计算机视觉|智能安全|machine learning|deep learning|neural network|artificial intelligence|digital twin|computer vision|large language"),
    ("安全人因工程", r"人因|人机|认知|疲劳|注意力|情绪|心理|不安全行为|human factor|human error|ergonomic|cognitive|fatigue|workload|situation awareness"),
    ("安全法治与政策", r"安全法|安全生产法|法规|法律|标准|政策|监管|治理|safety law|regulation|policy|governance"),
    ("安全教育与培训", r"安全教育|安全培训|培训|教育|演练|safety education|safety training|training"),
    ("韧性", r"韧性|恢复力|鲁棒性|resilience|robustness|recovery"),
]

TOPIC_COLORS = {
    "事故与伤害": "#B24B2A",
    "安全管理": "#8A6A2F",
    "安全文化": "#A16A3A",
    "地质灾害与防灾": "#6B6A4F",
    "风险评估与管控": "#256C7A",
    "应急与救援": "#4B6E8A",
    "火灾与消防": "#C25A2E",
    "职业健康": "#4F7A62",
    "矿山安全": "#5A6B3B",
    "化工与过程安全": "#8A4B57",
    "建筑施工安全": "#B8653A",
    "交通安全": "#3F5F8A",
    "公共安全与城市安全": "#6B6A8A",
    "安全智能化": "#4F6D8C",
    "安全人因工程": "#7A5C8E",
    "安全法治与政策": "#6F5B4A",
    "安全教育与培训": "#8A7A3A",
    "韧性": "#3C6E71",
    "安全科学综合": "#8A8578",
}

DEFAULT_SETTINGS = {
    "daily_time": "08:00",
    "watch_keywords": ["risk", "resilience", "human factors", "fire", "evacuation", "construction", "事故", "安全管理", "安全文化"],
    "research_interests": ["大模型", "数据驱动", "智能体", "算法"],
    "watched_authors": [],
    "sync_on_start": False,
    "digest_scope": "today",
    "compact": False,
    "citation_refreshed_at": "",
}

DAILY_TASK_SCRIPT = APP_DIR.parent / "scripts" / "install_scheduler.ps1"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)