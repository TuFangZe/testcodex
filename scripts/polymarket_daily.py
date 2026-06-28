from __future__ import annotations

import json
import math
import os
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from gmail_sender import GmailSendError, send_message


GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search?q={query}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_TIMEZONE = os.getenv("POLYMARKET_REPORT_TZ", "Asia/Shanghai")
DEFAULT_RECIPIENTS = os.getenv("GMAIL_TO", "")
DRY_RUN = os.getenv("POLYMARKET_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}
OUTPUT_DIR = Path("outputs")


SEARCH_QUERIES = [
    "gold",
    "silver",
    "bitcoin",
    "ethereum",
    "solana",
    "microstrategy",
    "tesla",
    "spy",
    "spx",
    "qqq",
    "nasdaq",
    "nvda",
    "meta",
]

YAHOO_TICKERS = {
    "gold_futures": "GC=F",
    "silver_futures": "SI=F",
    "gld": "GLD",
    "slv": "SLV",
    "gdx": "GDX",
    "btc": "BTC-USD",
    "eth": "ETH-USD",
    "sol": "SOL-USD",
    "mstr": "MSTR",
    "tsla": "TSLA",
    "spy": "SPY",
    "qqq": "QQQ",
    "nvda": "NVDA",
    "meta": "META",
    "tnx": "^TNX",
}

NEWS_QUERIES = {
    "precious_metals": "gold silver precious metals markets",
    "crypto": "bitcoin ethereum crypto ETF",
    "equities": "Tesla deliveries Strategy bitcoin purchase stock market",
}

SOURCE_LINKS = [
    ("Polymarket", "https://polymarket.com/"),
    ("Polymarket Gamma API", "https://gamma-api.polymarket.com/"),
    ("Yahoo Finance", "https://finance.yahoo.com/"),
    ("Google News", "https://news.google.com/"),
    ("Strategy Purchases", "https://www.strategy.com/purchases"),
    ("Tesla IR Delivery Consensus", "https://ir.tesla.com/press-release/delivery-consensus-second-quarter-2026"),
    ("World Gold Council", "https://www.gold.org/goldhub"),
    ("Silver Institute", "https://silverinstitute.org/"),
]


@dataclass
class MarketSignal:
    question: str
    slug: str
    event_title: str
    event_slug: str
    outcomes: list[str]
    outcome_prices: list[float]
    probability: float | None
    last_trade_price: float | None
    one_day_price_change: float | None
    volume_24h: float
    liquidity: float
    end_date_iso: str
    best_bid: float | None
    best_ask: float | None
    theme: str
    signal_strength: str
    score: float
    source_query: str

    @property
    def event_url(self) -> str:
        slug = self.event_slug or self.slug
        return f"https://polymarket.com/event/{slug}"


@dataclass
class PriceSnapshot:
    label: str
    ticker: str
    price: float | None
    previous_close: float | None
    day_change_pct: float | None
    currency: str | None
    source_url: str


@dataclass
class NewsItem:
    bucket: str
    title: str
    link: str
    published: str


@dataclass
class ReportPayload:
    report_date: str
    generated_at: str
    timezone: str
    signals: list[MarketSignal] = field(default_factory=list)
    prices: list[PriceSnapshot] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def json_get(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    request = urllib.request.Request(
        url,
        headers=headers
        or {
            "User-Agent": "Mozilla/5.0 (compatible; polymarket-daily-report/1.0)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def text_get(url: str, *, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; polymarket-daily-report/1.0)",
            "Accept": "text/plain,text/html,application/xml,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def parse_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_theme(question: str, source_query: str) -> str:
    lowered = f"{question} {source_query}".lower()
    if any(token in lowered for token in ("gold", "silver", "xau", "xag", "(gc)", "(si)", "precious")):
        return "precious_metals"
    if any(token in lowered for token in ("bitcoin", "btc", "microstrategy", "strategy", "mstr")):
        return "crypto_btc"
    if any(token in lowered for token in ("ethereum", "eth")):
        return "crypto_eth"
    if any(token in lowered for token in ("solana", "sol")):
        return "crypto_sol"
    if "tesla" in lowered or "tsla" in lowered:
        return "equities_tsla"
    if any(token in lowered for token in ("spy", "spx", "qqq", "nasdaq", "s&p", "ndx")):
        return "equities_index"
    if any(token in lowered for token in ("nvidia", "nvda", "meta", "pltr", "apple", "amazon", "microsoft")):
        return "equities_single_name"
    return "other"


def strength_label(probability_change: float | None, volume_24h: float, liquidity: float) -> tuple[str, float]:
    change = abs(probability_change or 0.0)
    score = change * (math.log1p(max(volume_24h, 0.0)) + math.log1p(max(liquidity, 0.0)))
    if change >= 0.05 and volume_24h >= 5000:
        return "很强", score
    if change >= 0.04 and (volume_24h >= 500 or liquidity >= 5000):
        return "强", score
    if change >= 0.02 and (volume_24h >= 100 or liquidity >= 1000):
        return "中等", score
    return "弱", score


def extract_markets_from_search(query: str) -> list[MarketSignal]:
    encoded = urllib.parse.quote(query)
    payload = json_get(GAMMA_SEARCH_URL.format(query=encoded))
    signals: list[MarketSignal] = []
    seen: set[str] = set()

    def append_market(raw_market: dict[str, Any], event_title: str = "", event_slug: str = "") -> None:
        slug = raw_market.get("slug")
        if not slug or slug in seen:
            return
        if raw_market.get("closed") or not raw_market.get("active"):
            return
        outcomes = parse_json_list(raw_market.get("outcomes"))
        outcome_prices = [x for x in (parse_float(item) for item in parse_json_list(raw_market.get("outcomePrices"))) if x is not None]
        probability = outcome_prices[0] if outcome_prices else parse_float(raw_market.get("lastTradePrice"))
        volume_24h = parse_float(raw_market.get("volume24hr")) or 0.0
        liquidity = parse_float(raw_market.get("liquidityNum")) or parse_float(raw_market.get("liquidity")) or 0.0
        best_bid = parse_float(raw_market.get("bestBid"))
        best_ask = parse_float(raw_market.get("bestAsk"))
        one_day_price_change = parse_float(raw_market.get("oneDayPriceChange"))
        strength, score = strength_label(one_day_price_change, volume_24h, liquidity)
        seen.add(slug)
        signals.append(
            MarketSignal(
                question=raw_market.get("question", ""),
                slug=slug,
                event_title=event_title,
                event_slug=event_slug,
                outcomes=outcomes,
                outcome_prices=outcome_prices,
                probability=probability,
                last_trade_price=parse_float(raw_market.get("lastTradePrice")),
                one_day_price_change=one_day_price_change,
                volume_24h=volume_24h,
                liquidity=liquidity,
                end_date_iso=raw_market.get("endDateIso", "") or "",
                best_bid=best_bid,
                best_ask=best_ask,
                theme=classify_theme(raw_market.get("question", ""), query),
                signal_strength=strength,
                score=score,
                source_query=query,
            )
        )

    for raw_market in payload.get("markets", []) or []:
        append_market(raw_market)

    for event in payload.get("events", []) or []:
        event_title = event.get("title", "")
        event_slug = event.get("slug", "")
        for raw_market in event.get("markets", []) or []:
            append_market(raw_market, event_title=event_title, event_slug=event_slug)

    return signals


def collect_polymarket_signals() -> list[MarketSignal]:
    signals: dict[str, MarketSignal] = {}
    for query in SEARCH_QUERIES:
        try:
            for signal in extract_markets_from_search(query):
                existing = signals.get(signal.slug)
                if existing is None or signal.score > existing.score:
                    signals[signal.slug] = signal
        except Exception as exc:  # pragma: no cover - defensive logging path
            print(f"[warn] failed to fetch Polymarket query={query}: {exc}", file=sys.stderr)

    theme_weights = {
        "precious_metals": 1.35,
        "crypto_btc": 1.35,
        "crypto_eth": 1.3,
        "crypto_sol": 1.1,
        "equities_tsla": 1.15,
        "equities_index": 1.0,
        "equities_single_name": 0.7,
        "other": 0.5,
    }

    filtered: list[MarketSignal] = []
    for signal in signals.values():
        if signal.one_day_price_change is None:
            continue
        if signal.volume_24h < 50 and signal.liquidity < 1500:
            continue
        if signal.theme in {"equities_single_name", "other"} and signal.volume_24h < 500 and signal.liquidity < 5000:
            continue
        if signal.theme in {"precious_metals", "crypto_btc", "crypto_eth", "equities_tsla"} and signal.volume_24h < 100 and signal.liquidity < 2000:
            continue
        signal.score *= theme_weights.get(signal.theme, 1.0)
        filtered.append(signal)

    filtered.sort(key=lambda item: (item.score, abs(item.one_day_price_change or 0.0), item.volume_24h), reverse=True)

    per_theme_limits = {
        "precious_metals": 4,
        "crypto_btc": 4,
        "crypto_eth": 3,
        "crypto_sol": 2,
        "equities_tsla": 4,
        "equities_index": 3,
        "equities_single_name": 2,
        "other": 1,
    }

    picked: list[MarketSignal] = []
    picked_slugs: set[str] = set()
    for theme, limit in per_theme_limits.items():
        theme_items = [item for item in filtered if item.theme == theme][:limit]
        for item in theme_items:
            if item.slug not in picked_slugs:
                picked.append(item)
                picked_slugs.add(item.slug)

    for item in filtered:
        if len(picked) >= 18:
            break
        if item.slug not in picked_slugs:
            picked.append(item)
            picked_slugs.add(item.slug)

    picked.sort(key=lambda item: (item.score, abs(item.one_day_price_change or 0.0), item.volume_24h), reverse=True)
    return picked[:18]


def fetch_yahoo_snapshot(label: str, ticker: str) -> PriceSnapshot:
    encoded = urllib.parse.quote(ticker)
    payload = json_get(YAHOO_CHART_URL.format(ticker=encoded))
    result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
    meta = result.get("meta") or {}
    price = parse_float(meta.get("regularMarketPrice"))
    previous_close = parse_float(meta.get("chartPreviousClose")) or parse_float(meta.get("previousClose"))
    if price is not None and previous_close not in (None, 0):
        day_change_pct = ((price - previous_close) / previous_close) * 100.0
    else:
        day_change_pct = None
    return PriceSnapshot(
        label=label,
        ticker=ticker,
        price=price,
        previous_close=previous_close,
        day_change_pct=day_change_pct,
        currency=meta.get("currency"),
        source_url=f"https://finance.yahoo.com/quote/{urllib.parse.quote(ticker)}",
    )


def collect_price_snapshots() -> list[PriceSnapshot]:
    snapshots: list[PriceSnapshot] = []
    for label, ticker in YAHOO_TICKERS.items():
        try:
            snapshots.append(fetch_yahoo_snapshot(label, ticker))
        except Exception as exc:  # pragma: no cover - defensive logging path
            print(f"[warn] failed to fetch Yahoo ticker={ticker}: {exc}", file=sys.stderr)
    return snapshots


def fetch_news_bucket(bucket: str, query: str) -> list[NewsItem]:
    encoded = urllib.parse.quote(query)
    xml_text = text_get(GOOGLE_NEWS_RSS_URL.format(query=encoded))
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []
    for item in root.findall("./channel/item")[:5]:
        items.append(
            NewsItem(
                bucket=bucket,
                title=(item.findtext("title") or "").strip(),
                link=(item.findtext("link") or "").strip(),
                published=(item.findtext("pubDate") or "").strip(),
            )
        )
    return items


def collect_news() -> list[NewsItem]:
    news: list[NewsItem] = []
    seen: set[str] = set()
    for bucket, query in NEWS_QUERIES.items():
        try:
            for item in fetch_news_bucket(bucket, query):
                if item.title and item.title not in seen:
                    seen.add(item.title)
                    news.append(item)
        except Exception as exc:  # pragma: no cover - defensive logging path
            print(f"[warn] failed to fetch news bucket={bucket}: {exc}", file=sys.stderr)
    return news[:12]


def summarize_payload_for_prompt(payload: ReportPayload) -> dict[str, Any]:
    return {
        "report_date": payload.report_date,
        "generated_at": payload.generated_at,
        "timezone": payload.timezone,
        "notes": payload.notes,
        "polymarket_signals": [
            {
                "question": item.question,
                "event_title": item.event_title,
                "event_url": item.event_url,
                "probability": item.probability,
                "one_day_price_change_pct_points": None
                if item.one_day_price_change is None
                else round(item.one_day_price_change * 100, 2),
                "volume_24h": round(item.volume_24h, 2),
                "liquidity": round(item.liquidity, 2),
                "best_bid": item.best_bid,
                "best_ask": item.best_ask,
                "theme": item.theme,
                "signal_strength": item.signal_strength,
                "end_date_iso": item.end_date_iso,
            }
            for item in payload.signals
        ],
        "price_snapshots": [
            {
                "label": item.label,
                "ticker": item.ticker,
                "price": item.price,
                "previous_close": item.previous_close,
                "day_change_pct": None if item.day_change_pct is None else round(item.day_change_pct, 2),
                "currency": item.currency,
                "source_url": item.source_url,
            }
            for item in payload.prices
        ],
        "news": [
            {
                "bucket": item.bucket,
                "title": item.title,
                "published": item.published,
                "link": item.link,
            }
            for item in payload.news
        ],
        "source_links": [{"label": label, "url": url} for label, url in SOURCE_LINKS],
    }


def build_openai_prompt(summary: dict[str, Any]) -> str:
    return textwrap.dedent(
        f"""
        你是一名严谨的中文市场研究员。请基于以下结构化数据，生成一份中文 Markdown 日报。

        硬性要求：
        1. 报告标题使用：Polymarket赔率交易日报 - {summary["report_date"]}
        2. 必须明确说明：Polymarket 的历史赔率时间序列并未完整抓取，本报告主要使用当前概率、过去24小时变化、成交量、流动性，以及外部价格/新闻做替代判断。
        3. 不要给个性化投资建议，要表述为“研究观察”“情景分析”“关注方向”。
        4. 结构必须严格包含以下 6 个章节：
           - 1) 今日结论
           - 2) Polymarket 信号
           - 3) 资产映射
           - 4) 交易理由
           - 5) 风险与失效条件
           - 6) 今日观察清单
        5. “今日结论”里列出 3-7 个最值得关注的标的或交易主题，并给出 偏多 / 偏空 / 观望。
        6. “交易理由”必须拆成：
           - 数据支持
           - 合理推断
           - 待确认假设
        7. 必须保留来源链接，末尾增加“来源”章节。
        8. 如果某些外部数据不足，就明确写“数据限制”。
        9. 优先关注与贵金属、股票、虚拟货币相关的主题，忽略无关市场。
        10. 输出不要使用 JSON，不要解释你的推理过程。

        下面是结构化数据：

        {json.dumps(summary, ensure_ascii=False, indent=2)}
        """
    ).strip()


def call_openai_for_report(summary: dict[str, Any]) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for model-generated report output.")

    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You write concise, factual Chinese market research reports in Markdown.",
            },
            {
                "role": "user",
                "content": build_openai_prompt(summary),
            },
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI network error: {exc}") from exc

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI response missing choices: {result}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        if texts:
            return "\n".join(texts).strip()
    raise RuntimeError(f"OpenAI response missing text content: {result}")


def fallback_report(payload: ReportPayload) -> str:
    lines = [
        f"# Polymarket赔率交易日报 - {payload.report_date}",
        "",
        "数据限制：Polymarket 的完整历史赔率时间序列未直接抓取，本报告主要依赖当前隐含概率、24 小时变化、成交量、流动性，以及外部价格快照与新闻标题做替代判断。",
        "",
        "## 1) 今日结论",
    ]
    top_signals = payload.signals[:5]
    for idx, signal in enumerate(top_signals, start=1):
        direction = "观望"
        question = signal.question.lower()
        change = signal.one_day_price_change or 0.0
        if any(token in question for token in ("dip", "low", "below", "less than")):
            direction = "偏多" if change < 0 else "偏空"
        elif any(token in question for token in ("high", "reach", "above", "up")):
            direction = "偏多" if change > 0 else "偏空"
        lines.append(
            f"{idx}. **{signal.question}**：{direction}。当前概率约 `{(signal.probability or 0) * 100:.1f}%`，"
            f"24 小时变化 `{change * 100:+.2f}` 个点，信号强度 `{signal.signal_strength}`。"
        )

    lines.extend(
        [
            "",
            "## 2) Polymarket 信号",
            "",
            "| 市场 | 当前隐含概率 | 24小时变化 | 24h成交 | 流动性 | 信号强弱 | 链接 |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for signal in payload.signals[:10]:
        probability = "N/A" if signal.probability is None else f"{signal.probability * 100:.1f}%"
        change = "N/A" if signal.one_day_price_change is None else f"{signal.one_day_price_change * 100:+.2f}pct"
        lines.append(
            f"| {signal.question} | {probability} | {change} | {signal.volume_24h:.0f} | {signal.liquidity:.0f} | {signal.signal_strength} | {signal.event_url} |"
        )

    lines.extend(
        [
            "",
            "## 3) 资产映射",
            "",
            "- 贵金属相关市场优先映射到 `GC=F` / `SI=F`、`GLD`、`SLV`、`GDX`。",
            "- 比特币与 Strategy 相关市场优先映射到 `BTC-USD`、`MSTR`，次级映射到 BTC ETF。",
            "- 以太坊相关市场优先映射到 `ETH-USD`，再观察 ETH ETF 与高 beta 加密代理。",
            "- Tesla 与指数市场分别映射到 `TSLA`、`SPY`、`QQQ`。",
            "",
            "## 4) 交易理由",
            "",
            "### 数据支持",
            "",
        ]
    )
    for snapshot in payload.prices[:8]:
        if snapshot.price is None:
            continue
        move = "N/A" if snapshot.day_change_pct is None else f"{snapshot.day_change_pct:+.2f}%"
        lines.append(f"- `{snapshot.ticker}` 最新快照约为 `{snapshot.price}`，日内变化 `{move}`。来源：{snapshot.source_url}")

    lines.extend(
        [
            "",
            "### 合理推断",
            "",
            "- Polymarket 24 小时变化更适合观察“边际重定价”，不适合把多档二元市场直接当成完整概率分布。",
            "- 若下探类市场概率回落，通常对应基础资产短线压力缓和；若上破类市场概率上升，通常对应风险偏好改善。",
            "",
            "### 待确认假设",
            "",
            "- 需要继续确认未来 24 小时内 ETF 资金流、宏观数据和公司公告是否跟进当前赔率变化。",
            "- 需要确认新闻标题对应的催化是否真正落地，而不只是情绪交易。",
            "",
            "## 5) 风险与失效条件",
            "",
            "- 低流动性市场可能出现盘口跳变，造成 24 小时变化失真。",
            "- 若外部价格与 Polymarket 赔率重新背离，当前情景分析需要下修。",
            "- 宏观新闻、监管消息和公司公告都可能在未来 24 小时快速改变方向。",
            "",
            "## 6) 今日观察清单",
            "",
        ]
    )
    for item in payload.news[:8]:
        lines.append(f"- `{item.bucket}`：[{item.title}]({item.link})")

    lines.extend(["", "## 来源", ""])
    for label, url in SOURCE_LINKS:
        lines.append(f"- [{label}]({url})")
    return "\n".join(lines).strip() + "\n"


def build_report_payload() -> ReportPayload:
    tz_name = DEFAULT_TIMEZONE
    now = datetime.now(ZoneInfo(tz_name))
    payload = ReportPayload(
        report_date=now.strftime("%Y-%m-%d"),
        generated_at=now.isoformat(),
        timezone=tz_name,
    )
    payload.signals = collect_polymarket_signals()
    payload.prices = collect_price_snapshots()
    payload.news = collect_news()
    payload.notes.append(
        "Polymarket 以当前隐含概率和 oneDayPriceChange 为主；部分市场 lastTradePrice 与盘口可能不一致。"
    )
    if not payload.signals:
        payload.notes.append("未抓到满足阈值的活跃市场，报告质量会明显下降。")
    if not payload.news:
        payload.notes.append("未抓到可用新闻 RSS，将更多依赖市场与价格快照。")
    return payload


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def write_outputs(report_date: str, report_body: str, payload: ReportPayload) -> tuple[Path, Path]:
    ensure_output_dir()
    markdown_path = OUTPUT_DIR / f"polymarket_report_{report_date}.md"
    json_path = OUTPUT_DIR / f"polymarket_payload_{report_date}.json"
    markdown_path.write_text(report_body, encoding="utf-8")
    json_path.write_text(
        json.dumps(summarize_payload_for_prompt(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return markdown_path, json_path


def parse_recipients(raw: str) -> list[str]:
    recipients = [item.strip() for item in raw.split(",") if item.strip()]
    if not recipients:
        raise RuntimeError("GMAIL_TO is empty. Set at least one recipient email address.")
    return recipients


def send_report_via_gmail(subject: str, body_text: str) -> dict:
    sender = os.getenv("GMAIL_SENDER", "").strip()
    client_id = os.getenv("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "").strip()
    recipients = parse_recipients(DEFAULT_RECIPIENTS)
    missing = [
        key
        for key, value in {
            "GMAIL_SENDER": sender,
            "GMAIL_CLIENT_ID": client_id,
            "GMAIL_CLIENT_SECRET": client_secret,
            "GMAIL_REFRESH_TOKEN": refresh_token,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing Gmail environment variables: {', '.join(missing)}")

    return send_message(
        sender=sender,
        recipients=recipients,
        subject=subject,
        body_text=body_text,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )


def main() -> int:
    payload = build_report_payload()
    summary = summarize_payload_for_prompt(payload)

    try:
        report_body = call_openai_for_report(summary)
    except Exception as exc:
        print(f"[warn] OpenAI report generation failed, using fallback template: {exc}", file=sys.stderr)
        payload.notes.append(f"OpenAI generation failed; fallback report used: {exc}")
        report_body = fallback_report(payload)

    subject = f"Polymarket赔率交易日报 - {payload.report_date}"
    markdown_path, json_path = write_outputs(payload.report_date, report_body, payload)
    print(f"[info] wrote report: {markdown_path}")
    print(f"[info] wrote payload: {json_path}")

    if DRY_RUN:
        print("[info] POLYMARKET_DRY_RUN is enabled; skipping Gmail send.")
        return 0

    try:
        gmail_result = send_report_via_gmail(subject, report_body)
    except (RuntimeError, GmailSendError) as exc:
        print(f"[error] failed to send Gmail report: {exc}", file=sys.stderr)
        return 1

    print(f"[info] Gmail send result: {json.dumps(gmail_result, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
