"""내 PC의 참조 폴더(과거 보고서, 용어집 등)에서 조직 고유 용어 사전을 구축한다.

인터넷 연결이 필요 없다. .txt / .hwpx / .hwp 파일을 읽어 명사(핵심 어휘)를
뽑아내고, 단어별 등장 빈도와 출처 파일명을 함께 기록한다.
"""
from __future__ import annotations

import glob
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .learned_corrections import load_learned_corrections
from .style_checker import classify_sentence_style, split_sentences

try:
    from konlpy.tag import Okt

    _okt: "Okt | None" = Okt()
except Exception:
    # Java(JDK)가 없거나 konlpy 미설치인 경우: 형태소 분석 없이 동작하도록 대체
    _okt = None

_HANGUL_WORD_RE = re.compile(r"[가-힣]{2,}")

# konlpy(Java) 없이 동작할 때, 어절 끝의 조사만 최대한 떼어내기 위한 대체 목록.
# 동사/형용사 어미(했다/습니다 등)는 명사 비교와 무관하므로 포함하지 않는다.
# 단, "이다"(서술격 조사)는 예외적으로 포함한다 — 문법적으로 조사이면서
# "홍길동입니다"처럼 이름·용어 바로 뒤에 붙는 매우 흔한 패턴이라, 이걸
# 안 떼어내면 "홍길동"이 참조 사전에 "홍길동입니다"라는 통째 항목으로만
# 등록되어 이후 오탈자 비교("횽길동" 등)에 전혀 쓸모가 없어진다.
_JOSA_SUFFIXES = sorted(
    [
        "으로서", "으로써", "에서부터", "까지는", "부터는", "에게서", "한테서",
        "이었습니다", "였습니다", "이었다", "였다", "입니다", "이다",
        "에서", "으로", "까지", "부터", "한테", "에게", "이나", "라도", "이라",
        "조차", "마저", "보다", "처럼", "만큼", "마다", "밖에",
        "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "께", "나", "랑", "로",
    ],
    key=len,
    reverse=True,
)


def _strip_josa(word: str) -> str:
    for suf in _JOSA_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            return word[: -len(suf)]
    return word


def _extract_nouns(text: str) -> list[str]:
    """텍스트에서 비교 기준이 될 핵심 단어(명사)를 뽑는다."""
    if _okt is not None:
        try:
            return [w for w in _okt.nouns(text) if len(w) > 1]
        except Exception:
            pass
    # konlpy를 쓸 수 없는 환경을 위한 대체 방식: 한글 어절에서 조사만 떼어낸다.
    return [_strip_josa(w) for w in _HANGUL_WORD_RE.findall(text)]


def core_word(token: str) -> "str | None":
    """한 어절(공백으로 구분된 단어)에서 비교 기준이 될 핵심 단어를 뽑는다.

    matcher.py의 오탈자 비교와 학습 사전 조회가 항상 같은 기준으로 단어를
    추출하도록, 이 함수 하나를 공유해서 쓴다.
    """
    nouns = _extract_nouns(token)
    if nouns:
        return max(nouns, key=len)
    m = _HANGUL_WORD_RE.search(token) or re.search(r"[가-힣]+", token)
    return m.group(0) if m else None


@dataclass
class ReferenceDB:
    word_freq: dict = field(default_factory=lambda: defaultdict(int))
    word_sources: dict = field(default_factory=lambda: defaultdict(set))
    style_counts: dict = field(default_factory=Counter)
    # 사람이 직접 알려준 정확 일치 교정 (핵심단어 -> 핵심단어) 과 그 이유
    exact_corrections: dict = field(default_factory=dict)
    exact_reasons: dict = field(default_factory=dict)

    def __contains__(self, word: str) -> bool:
        return word in self.word_freq

    @property
    def vocabulary(self):
        return list(self.word_freq.keys())

    @property
    def dominant_style(self):
        """참조 문서들에서 가장 많이 쓰인 문체('formal'/'polite'/'plain'/'casual'). 없으면 None."""
        if not self.style_counts:
            return None
        return max(self.style_counts, key=self.style_counts.get)

    def add_text(self, text: str, source: str) -> None:
        for noun in _extract_nouns(text):
            self.word_freq[noun] += 1
            self.word_sources[noun].add(source)
        for sent in split_sentences(text):
            style = classify_sentence_style(sent)
            if style:
                self.style_counts[style] += 1

    def add_learned_correction(self, wrong: str, correct: str, reason: str, source: str = "[학습됨]") -> None:
        """사람이 직접 알려준 오류를 사전에 편입한다.

        1) wrong의 핵심단어 -> correct의 핵심단어를 정확 일치 사전에 등록해서,
           다음부터는 유사도 계산 없이 즉시 잡히게 한다.
        2) correct 텍스트를 일반 참조 텍스트로도 추가해서, 어휘 빈도/유사 단어
           비교에도 도움이 되게 한다.
        """
        wrong_core = core_word(wrong)
        correct_core = core_word(correct)
        if not wrong_core or not correct_core or wrong_core == correct_core:
            return
        self.exact_corrections[wrong_core] = correct_core
        self.exact_reasons[wrong_core] = reason
        self.add_text(correct, source)


def _read_hwp_text_with(hwp, path: str) -> str:
    """이미 열려있는 Hwp 인스턴스로 파일을 열어 전체 텍스트를 추출한다."""
    hwp.open(path)
    hwp.init_scan()
    chunks: list[str] = []
    try:
        while True:
            state, text = hwp.get_text()
            if state <= 1:
                break
            if state in (2, 3, 4):
                chunks.append(text)
    finally:
        hwp.release_scan()
    return "\n".join(chunks)


def build_reference_db(folder_path: str, progress_cb=None) -> ReferenceDB:
    """folder_path 안의 문서들을 읽어 ReferenceDB를 만든다.

    progress_cb(done, total, filename)가 주어지면 파일 처리마다 호출한다.
    """
    db = ReferenceDB()
    os.makedirs(folder_path, exist_ok=True)

    txt_files = sorted(glob.glob(os.path.join(folder_path, "*.txt")))
    hwp_files = sorted(
        glob.glob(os.path.join(folder_path, "*.hwpx")) + glob.glob(os.path.join(folder_path, "*.hwp"))
    )
    all_files = txt_files + hwp_files

    hwp = None
    try:
        for i, path in enumerate(all_files):
            name = os.path.basename(path)
            try:
                if path in txt_files:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                else:
                    if hwp is None:
                        # 참조 폴더 스캔 전용 한/글 인스턴스는 화면에 띄우지 않는다.
                        from pyhwpx import Hwp

                        hwp = Hwp(visible=False)
                    text = _read_hwp_text_with(hwp, path)
                db.add_text(text, name)
            except Exception as exc:  # noqa: BLE001
                print(f"[참조 폴더] '{name}' 읽기 실패: {exc}")
            if progress_cb:
                progress_cb(i + 1, len(all_files), name)
    finally:
        if hwp is not None:
            try:
                hwp.quit()
            except Exception:
                pass

    for learned in load_learned_corrections(folder_path):
        db.add_learned_correction(learned.wrong, learned.correct, learned.reason, source="[학습됨]")

    return db
