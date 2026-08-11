"""조사(은/는, 이/가, 을/를, 과/와, 으로/로)의 받침 규칙 오류를 검사한다.

한글 조사는 앞 글자에 받침이 있는지 없는지에 따라 형태가 정해지는 순수
음운 규칙이다 (예: "책이"는 맞고 "책가"는 틀림 — '책'에 받침 ㄱ이 있으므로).
이 규칙은 예외가 거의 없어서, 참조 사전이나 형태소 분석 없이도 한글
유니코드 구조만으로 100% 결정적으로 검사할 수 있다.

※ 참고: "이/가"와 "을/를" 중 문맥상 어느 조사를 써야 맞는지(주격/목적격
선택 오류), 주어-서술어 호응처럼 문장의 뜻을 이해해야 하는 문법 오류는
이 방식으로 잡을 수 없다. 그런 오류는 실제 문장 의미를 분석해야 하므로
규칙 기반으로 신뢰성 있게 잡기 어렵다 — 이 모듈은 오직 "받침 규칙" 하나만
검사한다.
"""
from __future__ import annotations

from dataclasses import dataclass

# 받침 유무에 따라 형태가 갈리는 조사 쌍. 감지용 접미사는 길이가 긴 것부터
# 검사해야 "으로"가 "로"로 잘못 잘리지 않는다.
_JOSA_ENDINGS = sorted(["으로", "로", "은", "는", "이", "가", "을", "를", "과", "와"], key=len, reverse=True)

_TRAILING_PUNCT = ".,!?)\"'」』】 \t"


def _final_consonant_index(ch: str):
    code = ord(ch)
    if not (0xAC00 <= code <= 0xD7A3):
        return None
    return (code - 0xAC00) % 28


def _has_batchim(ch: str):
    idx = _final_consonant_index(ch)
    if idx is None:
        return None
    return idx != 0


def _is_rieul_batchim(ch: str) -> bool:
    return _final_consonant_index(ch) == 8  # 28개 받침 목록에서 'ㄹ'의 인덱스


def _expected_josa(detected: str, stem_last_char: str):
    batchim = _has_batchim(stem_last_char)
    if batchim is None:
        return None
    if detected in ("은", "는"):
        return "은" if batchim else "는"
    if detected in ("이", "가"):
        return "이" if batchim else "가"
    if detected in ("을", "를"):
        return "을" if batchim else "를"
    if detected in ("과", "와"):
        return "과" if batchim else "와"
    if detected in ("으로", "로"):
        return "로" if (not batchim or _is_rieul_batchim(stem_last_char)) else "으로"
    return None


@dataclass
class JosaIssue:
    index: int  # 문단을 공백 기준으로 나눴을 때의 어절 순번 (WordSuggestion과 동일한 규칙)
    original_word: str
    stem: str
    wrong_josa: str
    correct_josa: str
    corrected_word: str

    @property
    def reason(self) -> str:
        batchim_desc = "받침 있음" if _has_batchim(self.stem[-1]) else "받침 없음"
        return (
            f"'{self.stem}'는 {batchim_desc} — 받침 규칙상 조사는 '{self.correct_josa}'가 맞습니다"
            f" (현재 '{self.wrong_josa}').\n"
            f"참조 문서와 무관하게 한글 맞춤법 규칙으로 판단한 것입니다."
        )


def find_josa_issues(paragraph_text: str) -> list:
    issues = []
    for idx, word in enumerate(paragraph_text.split()):
        core_word = word.rstrip(_TRAILING_PUNCT)
        trailing = word[len(core_word):]
        if not core_word:
            continue

        matched_ending = None
        for ending in _JOSA_ENDINGS:
            if core_word.endswith(ending) and len(core_word) - len(ending) >= 1:
                matched_ending = ending
                break
        if matched_ending is None:
            continue

        stem = core_word[: -len(matched_ending)]
        expected = _expected_josa(matched_ending, stem[-1])
        if not expected or expected == matched_ending:
            continue

        issues.append(
            JosaIssue(
                index=idx,
                original_word=word,
                stem=stem,
                wrong_josa=matched_ending,
                correct_josa=expected,
                corrected_word=f"{stem}{expected}{trailing}",
            )
        )
    return issues
