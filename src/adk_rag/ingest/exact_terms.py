"""exact_terms 抽取：文号 / 产品代码 / 金额 / 日期 / 比例。

为什么需要独立通道（计划 3.8、6.2）：
- `english` 配置会把 `Circular [2022] No. 5` 拆成 `circular`/`2022`/`no`/`5`，
  把 `CD-2026-001` 拆成 `cd`/`2026`/`001`；
- 数字和编号一旦被 stemming/tokenize 改写，精确查询就会漏；
- 所以这里抽出来的字符串**不参与 stemming**，单独存 `exact_terms` 数组 + GIN。

归一化规则（ingest 与查询必须用同一套，否则永远匹配不上）：
1. Unicode NFKC（全角 → 半角，如 `％` → `%`）；
2. 小写；
3. 空白压缩为单个空格；
4. 同时产出"去空格"紧凑形式（`circular[2022]no.5`），
   用于吸收 `Circular[2022] No.5` / `Circular [2022] No. 5` 这类空格差异；
5. 不做词干化、不过滤数字。
"""

from __future__ import annotations

import re
import unicodedata

# 每个 chunk 最多保留多少条精确词，避免异常文档把数组撑大。
MAX_TERMS_PER_CHUNK = 64

_WS_RE = re.compile(r"\s+")

# 规则顺序有意义：先抽更具体的文号形式，再抽通用编号。
_DOC_NUMBER = re.compile(
    r"\b(?P<kind>circular|notice|guideline|bulletin)\s*\[\s*(?P<year>\d{4})\s*\]"
    r"\s*no\.?\s*(?P<num>\d+)",
    re.IGNORECASE,
)
_NOTICE_SERIAL = re.compile(r"\bnotice\s+(?P<year>\d{4})-(?P<serial>\d{1,4})\b", re.IGNORECASE)
_PRODUCT_CODE = re.compile(r"\b[A-Z]{2,}-\d{2,}-\d{3,}\b", re.IGNORECASE)
_MONEY = re.compile(
    r"\b(?P<ccy>usd|eur|gbp|cny|jpy|hkd|sgd)\s*(?P<amount>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?P<magnitude>million|billion|thousand|m|bn|k))?\b",
    re.IGNORECASE,
)
_MAGNITUDE = re.compile(
    r"\b(?P<amount>\d+(?:\.\d+)?)\s*(?P<magnitude>million|billion|thousand)\b", re.IGNORECASE
)
_ISO_DATE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_PERCENT = re.compile(r"\b(?P<value>\d+(?:\.\d+)?)\s*%")
_MONTH_DATE = re.compile(
    r"\b(?P<month>january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)

_MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def normalize_term(value: str) -> str:
    """小写 + NFKC + 压缩空白，保留标点与数字。"""
    text = unicodedata.normalize("NFKC", value)
    return _WS_RE.sub(" ", text).strip().casefold()


def compact_term(value: str) -> str:
    """去掉所有空白，用于吸收空格差异。"""
    return re.sub(r"\s+", "", value)


def extract_exact_terms(text: str) -> list[str]:
    """从文本中抽取精确词，返回**已归一化、已去重、已排序**的列表。

    返回两种形式（带空格 / 紧凑），查询侧同样产出两种，`&&` 命中任意一种即可。

    注意：先对整段文本做 NFKC，再做正则匹配。否则全角的 `３.２５％` 或
    `ＵＳＤ １００，０００` 不会命中 ASCII 正则——而用户从中文输入法复制过来的
    查询恰恰长这样。
    """
    if not text:
        return []

    text = unicodedata.normalize("NFKC", text)
    found: set[str] = set()

    for match in _DOC_NUMBER.finditer(text):
        found.add(_canonical(f"{match['kind']} [{match['year']}] no. {match['num']}"))
    for match in _NOTICE_SERIAL.finditer(text):
        found.add(_canonical(f"notice {match['year']}-{match['serial']}"))
    for match in _PRODUCT_CODE.finditer(text):
        found.add(_canonical(match.group(0)))
    for match in _MONEY.finditer(text):
        amount = match["amount"]
        magnitude = match["magnitude"]
        value = f"{match['ccy']} {amount}"
        if magnitude:
            value = f"{value} {magnitude}"
        found.add(_canonical(value))
    for match in _MAGNITUDE.finditer(text):
        found.add(_canonical(f"{match['amount']} {match['magnitude']}"))
    for match in _ISO_DATE.finditer(text):
        found.add(_canonical(
            f"{match['year']}-{match['month']}-{match['day']}"
        ))
    for match in _MONTH_DATE.finditer(text):
        month = _MONTHS.get(match["month"].lower())
        if month:
            found.add(_canonical(f"{match['year']}-{month}-{int(match['day']):02d}"))
    for match in _PERCENT.finditer(text):
        # 统一成 "3.25%"，避免 "3.25 %" 与 "3.25%" 两种写法不匹配。
        found.add(_canonical(f"{match['value']}%"))

    terms: list[str] = []
    for term in sorted(found):
        compact = compact_term(term)
        for candidate in (term, compact):
            if candidate and candidate not in terms:
                terms.append(candidate)
    return terms[:MAX_TERMS_PER_CHUNK]


def _canonical(value: str) -> str:
    return normalize_term(value)
