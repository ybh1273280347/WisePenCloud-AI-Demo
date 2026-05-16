import re
from typing import List, Optional, Pattern, Tuple

from .models import PageBlockDetection

_ANTI_CRAWL_SCAN_CHARS = 30000

_HIGH_CONFIDENCE_PATTERNS = (
    "cf-chl",
    "cf-turnstile",
    "challenge-platform",
    "challenges.cloudflare.com",
    "g-recaptcha",
    "grecaptcha",
    "www.google.com/recaptcha",
    "h-captcha",
    "hcaptcha",
    "api.hcaptcha.com",
)

_SPA_SHELL_PATTERNS = (
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    "__next",
    "webpack",
    "vite",
)

_BOT_CHALLENGE_REGEXES = (
    re.compile(r"\bjust a moment\b", re.IGNORECASE),
    re.compile(r"\bchecking your browser\b", re.IGNORECASE),
    re.compile(r"\bverify you are human\b", re.IGNORECASE),
    re.compile(r"\bverifying you are human\b", re.IGNORECASE),
    re.compile(r"\bare you a robot\b", re.IGNORECASE),
    re.compile(r"\bare you human\b", re.IGNORECASE),
)

_BOT_CHALLENGE_CN_PATTERNS = (
    "请完成验证",
    "安全验证",
)

_CAPTCHA_REGEXES = (re.compile(r"\bcaptcha\b", re.IGNORECASE),)

_CAPTCHA_CN_PATTERNS = (
    "验证码",
    "人机验证",
)

_JS_REQUIRED_REGEXES = (
    re.compile(r"\bplease enable javascript\b", re.IGNORECASE),
    re.compile(r"\bplease enable js\b", re.IGNORECASE),
    re.compile(r"\benable javascript to continue\b", re.IGNORECASE),
    re.compile(r"\bjavascript is required\b", re.IGNORECASE),
    re.compile(r"\brequires javascript\b", re.IGNORECASE),
)

_JS_REQUIRED_CN_PATTERNS = (
    "请开启 javascript",
    "请启用 javascript",
)

_ACCESS_DENIED_REGEXES = (
    re.compile(r"\baccess denied\b", re.IGNORECASE),
    re.compile(r"\brequest blocked\b", re.IGNORECASE),
    re.compile(r"\bforbidden\b", re.IGNORECASE),
)

_ACCESS_DENIED_CN_PATTERNS = (
    "访问受限",
    "请求被拦截",
    "没有权限",
)

_RATE_LIMIT_REGEXES = (
    re.compile(r"\btoo many requests\b", re.IGNORECASE),
    re.compile(r"\brate limit\b", re.IGNORECASE),
    re.compile(r"\btemporarily blocked\b", re.IGNORECASE),
    re.compile(r"\bunusual traffic\b", re.IGNORECASE),
)

_RATE_LIMIT_CN_PATTERNS = (
    "访问过于频繁",
    "请求过于频繁",
)

_AUTH_REQUIRED_REGEXES = (
    re.compile(r"\bplease sign in\b", re.IGNORECASE),
    re.compile(r"\bplease log in\b", re.IGNORECASE),
    re.compile(r"\blogin required\b", re.IGNORECASE),
    re.compile(r"\bsign in to continue\b", re.IGNORECASE),
    re.compile(r"\blog in to continue\b", re.IGNORECASE),
    re.compile(r"\bunauthorized\b", re.IGNORECASE),
)

_AUTH_REQUIRED_CN_PATTERNS = (
    "需要登录",
    "请登录",
    "登录后查看",
    "请先登录",
)


def _match_substrings(text: str, patterns: Tuple[str, ...]) -> List[str]:
    return [pattern for pattern in patterns if pattern in text]


def _match_regexes(text: str, patterns: Tuple[Pattern[str], ...]) -> List[str]:
    return [pattern.pattern for pattern in patterns if pattern.search(text)]


def _match_mixed_patterns(
    text: str,
    *,
    regexes: Tuple[Pattern[str], ...],
    cn_patterns: Tuple[str, ...],
) -> List[str]:
    return _match_regexes(text, regexes) + _match_substrings(text, cn_patterns)


def detect_page_block(
    text: str,
    *,
    html: str = "",
    title: str = "",
    url: str = "",
    status_code: Optional[int] = None,
) -> PageBlockDetection:
    if not text and not html and not title and not url and status_code is None:
        return PageBlockDetection()

    sample = "\n".join(
        part[:_ANTI_CRAWL_SCAN_CHARS] for part in (title, url, html, text) if part
    )
    lower = sample.lower()

    visible_text = text[:_ANTI_CRAWL_SCAN_CHARS]
    visible_length = len(re.sub(r"\s+", "", visible_text))

    signals: List[str] = []

    if status_code == 401:
        return PageBlockDetection(
            kind="auth_required",
            confidence=0.95,
            score=5,
            signals=["http_status:401"],
        )

    if status_code == 403:
        return PageBlockDetection(
            kind="access_denied",
            confidence=0.95,
            score=5,
            signals=["http_status:403"],
        )

    if status_code == 429:
        return PageBlockDetection(
            kind="rate_limited",
            confidence=0.95,
            score=5,
            signals=["http_status:429"],
        )

    high_confidence_hits = _match_substrings(lower, _HIGH_CONFIDENCE_PATTERNS)
    if high_confidence_hits:
        return PageBlockDetection(
            kind=(
                "captcha"
                if any(
                    needle in value
                    for value in high_confidence_hits
                    for needle in ("captcha", "recaptcha", "hcaptcha", "turnstile")
                )
                else "bot_challenge"
            ),
            confidence=0.95,
            score=5,
            signals=[f"high_confidence:{hit}" for hit in high_confidence_hits[:5]],
        )

    groups = [
        ("bot_challenge", _BOT_CHALLENGE_REGEXES, _BOT_CHALLENGE_CN_PATTERNS, 2),
        ("captcha", _CAPTCHA_REGEXES, _CAPTCHA_CN_PATTERNS, 2),
        ("js_required", _JS_REQUIRED_REGEXES, _JS_REQUIRED_CN_PATTERNS, 2),
        ("access_denied", _ACCESS_DENIED_REGEXES, _ACCESS_DENIED_CN_PATTERNS, 2),
        ("rate_limited", _RATE_LIMIT_REGEXES, _RATE_LIMIT_CN_PATTERNS, 2),
        ("auth_required", _AUTH_REQUIRED_REGEXES, _AUTH_REQUIRED_CN_PATTERNS, 2),
    ]

    best_kind = "normal"
    best_score = 0

    for kind, regexes, cn_patterns, single_hit_score in groups:
        hits = _match_mixed_patterns(
            lower,
            regexes=regexes,
            cn_patterns=cn_patterns,
        )

        if len(hits) >= 2:
            return PageBlockDetection(
                kind=kind,
                confidence=0.9,
                score=4,
                signals=[f"{kind}:{hit}" for hit in hits[:5]],
            )

        if len(hits) == 1:
            score = single_hit_score
            signals.append(f"{kind}:{hits[0]}")

            if visible_length < 500:
                score += 1
                if "short_visible_text" not in signals:
                    signals.append("short_visible_text")

            if score > best_score:
                best_score = score
                best_kind = kind

    spa_hits = _match_substrings(lower, _SPA_SHELL_PATTERNS)
    if visible_length < 300 and spa_hits:
        score = best_score + 2
        return PageBlockDetection(
            kind="spa_shell",
            confidence=0.75 if score >= 3 else 0.6,
            score=score,
            signals=(signals + [f"spa_shell:{hit}" for hit in spa_hits[:5]])[:8],
        )

    if best_score >= 3:
        return PageBlockDetection(
            kind=best_kind,
            confidence=0.75,
            score=best_score,
            signals=signals[:8],
        )

    return PageBlockDetection(
        kind="normal",
        confidence=0.0,
        score=best_score,
        signals=signals[:8],
    )


def should_reject_page_block(
    detection: PageBlockDetection,
    *,
    stage: str,
) -> bool:
    if detection.kind == "normal":
        return False

    if stage == "raw_html":
        return _should_reject_raw_html_detection(detection)

    if stage in {"extracted_text", "plain_text"}:
        return _should_reject_visible_text_detection(detection)

    return False


def _should_reject_raw_html_detection(detection: PageBlockDetection) -> bool:
    if detection.kind in {"captcha", "bot_challenge"}:
        return detection.confidence >= 0.95 or detection.score >= 5

    if detection.kind in {"auth_required", "access_denied", "rate_limited"}:
        return any(signal.startswith("http_status:") for signal in detection.signals)

    return False


def _should_reject_visible_text_detection(detection: PageBlockDetection) -> bool:
    if detection.kind in {
        "bot_challenge",
        "captcha",
        "auth_required",
        "access_denied",
        "rate_limited",
        "js_required",
        "spa_shell",
    }:
        return detection.confidence >= 0.7 or detection.score >= 3

    return False
