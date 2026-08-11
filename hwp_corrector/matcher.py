"""문단 텍스트를 참조 사전과 비교해 오탈자/문맥 교정 제안을 만든다."""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from .reference_db import ReferenceDB, core_word

# 기본 커트라인 (5글자 이상 단어에 적용). 단어가 짧을수록 글자 1개 차이의
# 비중이 커서 유사도가 확 떨어지므로, 길이별로 커트라인을 낮춰준다.
# 짧을수록 커트라인이 낮아져 이름 같은 짧은 단어의 오탈자도 잡히지만,
# 그만큼 관계없는 단어끼리 오탐될 위험도 함께 커진다.
#
# 2글자 단어는 아예 퍼지 비교 대상에서 제외한다. 실제로 테스트해보니
# "다시"/"다음", "여기"/"저기", "회의"/"회신", "부서"/"부장"처럼 완전히
# 멀쩡하고 무관한 2글자 단어 쌍 다수가 커트라인 0.5를 넘어버려(2글자 중
# 1글자만 같아도 ratio가 정확히 0.5가 됨), 참조 사전에 우연히 없는
# 흔한 단어를 엉뚱한 단어로 "교정" 제안하는 오탐이 너무 잦았다. 2글자
# 단어의 오탈자를 잡고 싶다면 "학습" 기능(정확 일치라 오탐이 없음)을 쓰는
# 것이 안전하다.
SIMILARITY_CUTOFF = 0.72
MIN_FUZZY_LENGTH = 3
_LENGTH_CUTOFFS = [
    (4, 0.6),
    (6, 0.68),
]


def _cutoff_for_length(length: int) -> float:
    for max_len, cutoff in _LENGTH_CUTOFFS:
        if length <= max_len:
            return cutoff
    return SIMILARITY_CUTOFF


@dataclass
class WordSuggestion:
    index: int  # 문단을 공백 기준으로 나눴을 때의 어절 순번
    original_word: str  # 문단 안의 원래 어절 (조사 포함, 예: "행장안전부에서")
    core: str  # 비교에 사용한 핵심 단어 (예: "행장안전부")
    suggested_core: str  # 참조 사전에서 찾은 대체 단어 (예: "행정안전부")
    corrected_word: str  # 교정 반영 시 어절 전체 (예: "행정안전부에서")
    score: float  # 문자열 유사도 (0~1). 학습된 정확 일치인 경우 1.0
    freq: int  # 참조 문서 내 등장 횟수
    sources: list
    learned: bool = False  # 사람이 직접 알려준 교정인지 여부
    learned_reason: str = ""

    @property
    def reason(self) -> str:
        if self.learned:
            extra = f" 이유: {self.learned_reason}" if self.learned_reason else ""
            return (
                f"이전에 사람이 직접 확인해 알려준 오류입니다 (정확히 일치할 때만 적용됩니다).{extra}"
            )
        shown = sorted(self.sources)[:3]
        more = "" if len(self.sources) <= 3 else f" 외 {len(self.sources) - 3}건"
        pct = round(self.score * 100)
        return (
            f"참조 문서에서 '{self.suggested_core}'(이)가 {self.freq}회 발견됨"
            f" (출처: {', '.join(shown)}{more}).\n"
            f"입력하신 '{self.core}'와(과) 글자 유사도 {pct}%로 오탈자일 가능성이 있습니다."
        )


def find_paragraph_suggestions(text: str, ref_db: ReferenceDB) -> list:
    """문단 텍스트를 어절 단위로 쪼개 참조 사전에 없는 단어의 교정안을 찾는다."""
    suggestions: list = []
    words = text.split()
    vocab = ref_db.vocabulary

    for idx, word in enumerate(words):
        core = core_word(word)
        if not core or len(core) <= 1:
            continue

        if core in ref_db.exact_corrections:
            suggested = ref_db.exact_corrections[core]
            corrected_word = word.replace(core, suggested, 1)
            if corrected_word != word:
                suggestions.append(
                    WordSuggestion(
                        index=idx, original_word=word, core=core, suggested_core=suggested,
                        corrected_word=corrected_word, score=1.0,
                        freq=ref_db.word_freq.get(suggested, 0),
                        sources=sorted(ref_db.word_sources.get(suggested, [])),
                        learned=True, learned_reason=ref_db.exact_reasons.get(core, ""),
                    )
                )
            continue

        if core in ref_db or not vocab:
            continue
        if len(core) < MIN_FUZZY_LENGTH:
            continue

        cutoff = _cutoff_for_length(len(core))
        matches = difflib.get_close_matches(core, vocab, n=1, cutoff=cutoff)
        if not matches:
            continue

        suggested = matches[0]
        score = difflib.SequenceMatcher(None, core, suggested).ratio()
        corrected_word = word.replace(core, suggested, 1)
        if corrected_word == word:
            continue

        suggestions.append(
            WordSuggestion(
                index=idx,
                original_word=word,
                core=core,
                suggested_core=suggested,
                corrected_word=corrected_word,
                score=score,
                freq=ref_db.word_freq[suggested],
                sources=sorted(ref_db.word_sources[suggested]),
            )
        )
    return suggestions
