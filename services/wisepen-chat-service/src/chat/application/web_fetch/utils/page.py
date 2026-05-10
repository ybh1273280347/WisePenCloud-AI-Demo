import re
from dataclasses import dataclass, field
from typing import Optional, List, Tuple


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

_BOT_CHALLENGE_PATTERNS = (
    "just a moment",
    "checking your browser",
    "verify you are human",
    "verifying you are human",
    "are you a robot",
    "are you human",
    "请完成验证",
    "安全验证",
)

_CAPTCHA_PATTERNS = (
    "captcha",
    "验证码",
    "人机验证",
)

_JS_REQUIRED_PATTERNS = (
    "please enable javascript",
    "please enable js",
    "enable javascript to continue",
    "javascript is required",
    "requires javascript",
    "请开启 javascript",
    "请启用 javascript",
)

_ACCESS_DENIED_PATTERNS = (
    "access denied",
    "request blocked",
    "forbidden",
    "访问受限",
    "请求被拦截",
    "没有权限",
)

_RATE_LIMIT_PATTERNS = (
    "too many requests",
    "rate limit",
    "temporarily blocked",
    "unusual traffic",
    "访问过于频繁",
    "请求过于频繁",
)

_AUTH_REQUIRED_PATTERNS = (
    "please sign in",
    "please log in",
    "login required",
    "sign in to continue",
    "log in to continue",
    "unauthorized",
    "需要登录",
    "请登录",
    "登录后查看",
    "请先登录",
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


def _match_patterns(text: str, patterns: Tuple[str, ...]) -> List[str]:
    return [pattern for pattern in patterns if pattern in text]


@dataclass(slots=True)
class PageBlockDetection:
    kind: str = "normal"
    confidence: float = 0.0
    score: int = 0
    signals: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.kind != "normal"


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
        part[:_ANTI_CRAWL_SCAN_CHARS]
        for part in (title, url, html, text)
        if part
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

    high_confidence_hits = _match_patterns(lower, _HIGH_CONFIDENCE_PATTERNS)
    if high_confidence_hits:
        return PageBlockDetection(
            kind="captcha" if any(needle in value for value in high_confidence_hits for needle in ("captcha", "recaptcha", "hcaptcha", "turnstile")) else "bot_challenge",
            confidence=0.95,
            score=5,
            signals=[f"high_confidence:{hit}" for hit in high_confidence_hits[:5]],
        )

    groups = [
        ("bot_challenge", _BOT_CHALLENGE_PATTERNS, 2),
        ("captcha", _CAPTCHA_PATTERNS, 2),
        ("js_required", _JS_REQUIRED_PATTERNS, 2),
        ("access_denied", _ACCESS_DENIED_PATTERNS, 2),
        ("rate_limited", _RATE_LIMIT_PATTERNS, 2),
        ("auth_required", _AUTH_REQUIRED_PATTERNS, 2),
    ]

    best_kind = "normal"
    best_score = 0

    for kind, patterns, single_hit_score in groups:
        hits = _match_patterns(lower, patterns)

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

    spa_hits = _match_patterns(lower, _SPA_SHELL_PATTERNS)
    if visible_length < 300 and spa_hits:
        score = best_score + 2
        return PageBlockDetection(
            kind="spa_shell",
            confidence=0.75 if score >= 3 else 0.6,
            score=score,
            signals=signals + [f"spa_shell:{hit}" for hit in spa_hits[:5]],
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


def should_degrade_detection(detection: PageBlockDetection) -> bool:
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





