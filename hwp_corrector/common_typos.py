"""한국인이 자주 틀리는 맞춤법 중, 문맥과 무관하게 규칙으로 판정 가능한 것만 잡는다.

여기서 다루는 것과 다루지 않는 것을 명확히 구분한다.

다룸 (그 자체로 항상, 또는 거의 항상 틀렸다고 판정할 수 있는 것):
  - "됬" (예: 됬어요) — 표준 활용형에 없음. 항상 "됐"의 오타.
  - "몇일"/"웬지"/"어의없"/"오랫만"/"희안하" — 표준어에 없는 흔한 오타.
  - "~하개" — 부사형 어미 "~하게"의 오타로 거의 항상 해석됨.
  - "않" — "~지 않다" 구성이 아닌데 쓰이면 "안"의 오타일 가능성이 높음
    ("지"가 바로 앞에 없는 "않"은 문법적으로 성립하지 않기 때문).
  - 문장 끝 마침표 누락 — 마침표/물음표/느낌표 없이 끝나는, 제목/항목이
    아닌 것으로 보이는 문장.

다루지 않음 (문맥·의미를 이해해야 판단 가능해서 규칙만으로는 신뢰할 수 없음):
  - "되/돼" — 뒤에 오는 어미에 따라 달라짐 (예: "되다"는 되, "돼요"는 돼).
    "안" 규칙과 달리 앞/뒤 한두 글자만 봐서는 판정할 수 없다.
  - "로써/로서" — 철자·받침 문제가 아니라 "수단"과 "자격"이라는 의미
    차이라서, 문장 뜻을 이해해야 어느 쪽이 맞는지 알 수 있다.
  이 두 가지는 억지로 규칙을 만들면 오탐이 많아 오히려 신뢰를 떨어뜨리므로
  넣지 않았다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .style_checker import looks_like_heading

_TRAILING_PUNCT = ".,!?)\"'」』】 \t"

# (틀린 표현, 올바른 표현) — 표준 맞춤법에 아예 없는 형태라 항상 교정 대상.
_BLANKET_TYPOS = [
    ("됬", "됐"),
    ("몇일", "며칠"),
    ("웬지", "왠지"),
    ("어의없", "어이없"),
    ("오랫만", "오랜만"),
    ("희안하", "희한하"),
]

# 어절 끝에서만 검사하는(접미사) 형태의 흔한 오타.
_SUFFIX_TYPOS = [
    ("하개", "하게"),
]

_END_PUNCT = (".", "!", "?", "…", "”", "’", ")", "」", "』")


@dataclass
class TypoIssue:
    index: int
    original_word: str
    corrected_word: str
    reason: str


@dataclass
class PunctuationIssue:
    sentence: str
    suggested_sentence: str

    @property
    def reason(self) -> str:
        return "문장이 마침표(.)/물음표(?)/느낌표(!) 없이 끝났습니다. 제목이나 항목 나열이면 무시하세요."


def find_common_typos(paragraph_text: str) -> list:
    """항상(또는 거의 항상) 틀린 것으로 볼 수 있는 표현을 찾는다."""
    issues: list = []
    words = paragraph_text.split()

    for idx, word in enumerate(words):
        corrected = word

        for wrong, correct in _BLANKET_TYPOS:
            if wrong in corrected:
                corrected = corrected.replace(wrong, correct)

        core = corrected.rstrip(_TRAILING_PUNCT)
        trailing = corrected[len(core):]
        for wrong, correct in _SUFFIX_TYPOS:
            if core.endswith(wrong) and len(core) - len(wrong) >= 1:
                core = core[: -len(wrong)] + correct
        corrected = core + trailing

        if corrected != word:
            issues.append(
                TypoIssue(
                    index=idx, original_word=word, corrected_word=corrected,
                    reason=f"'{word}'는 표준 맞춤법에 없는 흔한 오타로 보입니다. '{corrected}'가 맞습니다.",
                )
            )
            continue

        # "않"은 "~지 않다" 구성일 때만 성립한다. 그게 아니면 "안"의 오타로 본다.
        pos = word.find("않")
        if pos == -1:
            continue
        preceded_by_ji = (pos > 0 and word[pos - 1] == "지") or (
            pos == 0 and idx > 0 and words[idx - 1].endswith("지")
        )
        if preceded_by_ji:
            continue
        corrected = word[:pos] + "안" + word[pos + 1:]
        issues.append(
            TypoIssue(
                index=idx, original_word=word, corrected_word=corrected,
                reason=(
                    f"'않'은 \"~지 않다\" 구성일 때만 씁니다. '{word}' 앞에 '지'가 없어서, "
                    f"'안'의 오타로 보입니다."
                ),
            )
        )
    return issues


def find_missing_period_issues(paragraph_text: str) -> list:
    """마침표/물음표/느낌표 없이 끝나는, 제목·항목이 아닌 것으로 보이는 문단을 찾는다."""
    if "\t" in paragraph_text:
        return []  # 탭으로 구분된 표 셀 내용은 문장이 아닐 가능성이 높아 건너뜀
    text = paragraph_text.strip()
    if len(text) < 8 or len(text.split()) < 3:
        return []  # 너무 짧으면 제목/항목일 가능성이 높아 건너뜀
    if text.endswith(_END_PUNCT):
        return []
    if looks_like_heading(text):
        return []  # 번호·기호로 시작하는 제목/항목은 건너뜀
    return [PunctuationIssue(sentence=text, suggested_sentence=text + ".")]
