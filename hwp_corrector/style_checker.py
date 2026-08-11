"""문서 전체의 문체(합쇼체/해요체/평서체) 일관성을 검사한다.

참조 폴더 문서들의 문장 종결어미를 보고 "우리 조직이 주로 쓰는 문체"를
정하고, 검사할 문서에서 그 문체와 다른 문장을 찾아낸다. 자동으로 문장을
고쳐 쓰지는 않는다 (어미 변형은 문법적으로 까다로워 잘못 바꾸면 오히려
어색해지므로) — 대신 사용자가 직접 수정한 문장을 입력하면 그 문장으로
찾아 바꾸기를 실행한다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_TRAILING_PUNCT_RE = re.compile(r"[\s.!?)\"'」』】]+$")

# "1. 회의 개요", "가. 세부 내용"처럼 문단 맨 앞의 번호/기호 목록 표시.
# 이 뒤의 마침표는 문장이 끝난 게 아니라 번호 매기기이므로, 문장 경계로
# 보지 않도록 미리 보호해서 분리한다 (안 그러면 "1."과 "회의 개요"가
# 서로 다른 "문장"으로 쪼개져 버린다).
_LIST_MARKER_RE = re.compile(r"^\s*(\d{1,3}|[가-힣])\.\s+")

# 번호/기호로 시작하는 제목·항목은 "문장"이 아니라 명사구인 경우가 많아,
# 문체 판정 대상에서 제외한다 (예: "개요"가 "요"로 끝나 해요체로 오판되는 것 방지).
_HEADING_START_RE = re.compile(r"^\s*([0-9①-⑩ⅠⅡⅢⅣⅤ가-힣]{1,3}[.)]|[-*·ㅁㅇ□■○●▪▶])")


def looks_like_heading(text: str) -> bool:
    """번호/기호로 시작하는 제목·항목처럼 보이는지 판단한다 (문장이 아닐 가능성)."""
    return bool(_HEADING_START_RE.match(text.strip()))

# 합쇼체는 어간(먹/합/옵 등)의 받침과 무관하게 항상 "니다"/"니까"로 끝나므로
# (먹습니다/합니다/옵니다/봅니다 ...) 이 세 어미로 통일해서 검사한다.
# ("ㅂ니다"처럼 자모를 분리한 패턴은 완성형 한글 문자열에서 매치되지 않으므로 쓰지 않는다.)
_FORMAL_END_RE = re.compile(r"(니다|니까|십시오)$")

# 반말/구어체 종결어미. "했어"/"갔어"/"이야"처럼 요/다로 끝나지 않는 구어체 문장을
# 잡기 위한 목록. 긴 표현을 먼저 검사해야 "거든"이 "든"으로 잘못 잘리지 않는다.
_CASUAL_ENDINGS = tuple(
    sorted(["잖아", "거든", "거야", "이야", "래", "네", "지", "냐", "자", "니", "야", "어", "아"],
           key=len, reverse=True)
)

# 개조식(보고서·공문에서 서술어를 명사형으로 끝맺는 방식) 종결어미.
# "회의 진행함.", "자료 없음.", "결과 첨부됨."처럼 항상 이 네 글자 중 하나로 끝난다.
_NOMINAL_ENDINGS = ("함", "됨", "임", "음")

STYLE_LABELS = {
    "formal": "합쇼체 (예: ~습니다)",
    "polite": "해요체 (예: ~해요)",
    "plain": "평서체 (예: ~다)",
    "casual": "반말/구어체 (예: ~했어, ~이야)",
    "nominal": "개조식 (예: ~함, ~없음)",
}


def split_sentences(text: str) -> list:
    text = text.strip()
    m = _LIST_MARKER_RE.match(text)
    prefix, rest = (text[: m.end()], text[m.end():]) if m else ("", text)
    sentences = [s for s in _SENT_SPLIT_RE.split(rest) if s.strip()]
    if prefix:
        if sentences:
            sentences[0] = prefix + sentences[0]
        else:
            sentences = [prefix.strip()]
    return sentences


def classify_sentence_style(sentence: str):
    """문장 종결부를 보고 'formal'/'polite'/'plain'/'casual'/'nominal'/None(판단 불가)을 반환한다."""
    core = _TRAILING_PUNCT_RE.sub("", sentence.strip())
    if len(core) < 2:
        return None
    if _FORMAL_END_RE.search(core):
        return "formal"
    if core.endswith(("요", "죠")):
        return "polite"
    if core.endswith("다"):
        return "plain"
    if core.endswith(_CASUAL_ENDINGS):
        return "casual"
    if core.endswith(_NOMINAL_ENDINGS):
        return "nominal"
    return None


def build_style_profile(texts):
    """텍스트 목록에서 문장 종결 문체 빈도를 세어 (지배적 문체, 빈도표)를 반환한다."""
    counter = Counter()
    for text in texts:
        for sent in split_sentences(text):
            style = classify_sentence_style(sent)
            if style:
                counter[style] += 1
    dominant = counter.most_common(1)[0][0] if counter else None
    return dominant, counter


@dataclass
class StyleIssue:
    sentence: str
    detected_style: str
    expected_style: str

    @property
    def reason(self) -> str:
        return (
            f"참조 문서들은 주로 '{STYLE_LABELS[self.expected_style]}'를 쓰는데,\n"
            f"이 문장은 '{STYLE_LABELS[self.detected_style]}'로 쓰여 있어 문체가 어긋납니다."
        )


def find_style_issues(paragraph_text: str, dominant_style) -> list:
    if not dominant_style:
        return []
    issues = []
    for sent in split_sentences(paragraph_text):
        if looks_like_heading(sent):
            continue
        style = classify_sentence_style(sent)
        if style and style != dominant_style:
            issues.append(StyleIssue(sentence=sent.strip(), detected_style=style, expected_style=dominant_style))
    return issues
