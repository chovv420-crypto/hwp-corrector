"""실제 '한글' 창을 열어 문단을 순회하고, GUI 승인을 받아 반영/저장하는 백그라운드 스레드.

Windows + 한컴오피스(아래한글)가 설치된 환경에서만 동작한다 (pyhwpx가 COM으로
실제 한/글 프로그램을 원격 조종하기 때문). pyhwpx import는 run() 안에서만
이루어지므로, 이 모듈 자체는 Windows가 아닌 환경에서도 문제없이 import할 수 있다.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field

from .common_typos import find_common_typos, find_missing_period_issues
from .josa_checker import find_josa_issues
from .learned_corrections import add_learned_correction
from .matcher import find_paragraph_suggestions
from .reference_db import ReferenceDB
from .style_checker import find_style_issues


@dataclass
class CombinedIssue:
    """한 어절에 여러 종류(철자/흔한 오타/조사 규칙)의 오류가 동시에 있을 때의 합쳐진 교정안."""

    index: int
    original_word: str
    corrected_word: str
    reason: str


def merge_suggestions(vocab_suggestions: list, josa_issues: list, typo_issues: "list | None" = None) -> list:
    """어절 단위 교정 제안들(어휘 유사도/흔한 오타/조사 규칙)을 인덱스 기준으로 합친다.

    같은 어절에 두 종류 이상이 동시에 걸리면(예: "사앙를" -> 철자 오류 + 조사
    오류가 함께 있음), 어휘 교정 -> 흔한 오타 교정 -> 조사 규칙 순으로 이전
    단계의 결과에 다음 규칙을 다시 적용해 하나의 완전한 교정안으로 합친다.
    """
    typo_issues = typo_issues or []
    vocab_by_index = {s.index: s for s in vocab_suggestions}
    typo_by_index = {t.index: t for t in typo_issues}
    josa_by_index = {j.index: j for j in josa_issues}
    all_indices = set(vocab_by_index) | set(typo_by_index) | set(josa_by_index)

    merged: list = []
    for idx in sorted(all_indices):
        v, t, j = vocab_by_index.get(idx), typo_by_index.get(idx), josa_by_index.get(idx)
        present = [p for p in (v, t, j) if p is not None]
        if len(present) == 1:
            merged.append(present[0])
            continue

        original_word = present[0].original_word
        word = original_word
        reasons = []
        if v is not None:
            word = v.corrected_word
            reasons.append(v.reason)
        if t is not None:
            rechecked = find_common_typos(word)
            word = rechecked[0].corrected_word if rechecked else t.corrected_word
            reasons.append(t.reason)
        if j is not None:
            rechecked = find_josa_issues(word)
            word = rechecked[0].corrected_word if rechecked else j.corrected_word
            reasons.append(j.reason)

        merged.append(
            CombinedIssue(index=idx, original_word=original_word, corrected_word=word, reason="\n\n".join(reasons))
        )
    return merged


@dataclass
class SentenceIssue:
    """한 문장에 여러 종류(문체/마침표 등)의 문장 단위 이슈가 동시에 있을 때의 합쳐진 안내."""

    sentence: str
    suggested_sentence: str
    reason: str


def merge_sentence_issues(issues: list) -> list:
    """문장 단위 이슈(문체/마침표 등)를 원문 문장 기준으로 합친다.

    GUI는 이슈를 문장 텍스트를 키로 삼아 반영하기 때문에(_collect_style_replacements),
    같은 문장에 이슈가 두 개 이상 있는데 합치지 않으면 나중 항목이 앞의 항목을
    조용히 덮어써서 한쪽 수정이 사라진다. 그래서 여기서 미리 하나로 합친다.
    """
    by_sentence: dict = {}
    order: list = []
    for issue in issues:
        key = issue.sentence
        if key not in by_sentence:
            by_sentence[key] = []
            order.append(key)
        by_sentence[key].append(issue)

    merged: list = []
    for key in order:
        group = by_sentence[key]
        if len(group) == 1:
            merged.append(group[0])
            continue
        suggested = key
        for g in group:
            candidate = getattr(g, "suggested_sentence", None)
            if candidate and candidate != key:
                suggested = candidate
        reason = "\n\n".join(g.reason for g in group)
        merged.append(SentenceIssue(sentence=key, suggested_sentence=suggested, reason=reason))
    return merged


@dataclass
class ParagraphTask:
    index: int
    total: int
    original_text: str
    words: list
    suggestions: list
    style_issues: list = field(default_factory=list)


@dataclass
class ParagraphDecision:
    accepted_indices: set = field(default_factory=set)
    style_replacements: dict = field(default_factory=dict)  # 원문장 -> 사용자가 고친 문장
    stop: bool = False


@dataclass
class TeachRequest:
    """자동 검사기가 놓친 오류를 사람이 직접 알려줄 때 GUI가 보내는 메시지.

    ParagraphDecision과 달리, 이걸 보내도 다음 문단으로 넘어가지 않는다 —
    같은 문단을 보면서 몇 번이든 추가로 가르쳐줄 수 있다.
    """

    wrong: str
    correct: str
    reason: str = ""


def _scan_paragraphs(hwp) -> list:
    """init_scan()/get_text()로 문서를 처음부터 끝까지 훑어 문단 목록을 만든다.

    pyhwpx의 get_text()는 문서를 줄바꿈 단위로 반복 반환하며,
    state == 3은 '다음 문단'을 의미한다 (pyhwpx core.py 문서 참고).
    """
    hwp.init_scan()
    paragraphs: list = []
    buf: list = []
    try:
        while True:
            state, text = hwp.get_text()
            if state <= 1:  # 0: 텍스트 없음, 1: 리스트의 끝
                break
            if state == 3:  # 다음 문단으로 넘어감
                paragraphs.append("".join(buf))
                buf = []
            elif state in (2, 4):  # 일반 텍스트 / 제어문자 내부 텍스트
                buf.append(text)
            # state == 5 (제어문자를 빠져나옴)은 텍스트가 없으므로 무시
    finally:
        hwp.release_scan()
    if buf:
        paragraphs.append("".join(buf))
    return paragraphs


class CorrectionWorker(threading.Thread):
    """문서를 열어 문단별 교정 제안을 GUI로 보내고, 승인된 것만 실제 문서에 반영한다."""

    def __init__(self, input_path: str, output_path: str, ref_db: ReferenceDB, reference_folder: str,
                 to_gui: "queue.Queue", to_worker: "queue.Queue"):
        super().__init__(daemon=True)
        self.input_path = input_path
        self.output_path = output_path
        self.ref_db = ref_db
        self.reference_folder = reference_folder
        self.to_gui = to_gui
        self.to_worker = to_worker

    def _apply_teach(self, hwp, req: TeachRequest) -> None:
        try:
            hwp.find_replace(src=req.wrong, dst=req.correct, regex=False, direction="AllDoc", MatchCase=1)
        except Exception as exc:  # noqa: BLE001
            print(f"[학습] '{req.wrong}' -> '{req.correct}' 찾아 바꾸기 실패: {exc}")
        add_learned_correction(self.reference_folder, req.wrong, req.correct, req.reason)
        self.ref_db.add_learned_correction(req.wrong, req.correct, req.reason)
        self.to_gui.put(("taught", req.wrong, req.correct))

    def run(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        from pyhwpx import Hwp

        hwp = None
        corrected = 0
        timing = {"scan": 0.0, "suggest": 0.0, "apply": 0.0, "review_wait": 0.0}
        t_run_start = time.perf_counter()
        try:
            # visible=True: 원본 아래한글 편집창이 실제로 화면에 뜬다.
            hwp = Hwp(visible=True)
            hwp.open(self.input_path)

            t0 = time.perf_counter()
            paragraphs = _scan_paragraphs(hwp)
            timing["scan"] = time.perf_counter() - t0
            total = len(paragraphs)

            for idx, original_text in enumerate(paragraphs):
                if not original_text.strip():
                    continue

                t0 = time.perf_counter()
                vocab_suggestions = find_paragraph_suggestions(original_text, self.ref_db)
                josa_issues = find_josa_issues(original_text)
                typo_issues = find_common_typos(original_text)
                suggestions = merge_suggestions(vocab_suggestions, josa_issues, typo_issues)

                style_issues = find_style_issues(original_text, self.ref_db.dominant_style)
                style_issues += find_missing_period_issues(original_text)
                style_issues = merge_sentence_issues(style_issues)
                timing["suggest"] += time.perf_counter() - t0

                if not suggestions and not style_issues:
                    continue

                self.to_gui.put(
                    ParagraphTask(
                        index=idx, total=total, original_text=original_text,
                        words=original_text.split(), suggestions=suggestions,
                        style_issues=style_issues,
                    )
                )

                # 사람이 "다음 문단"을 누르기 전까지, 같은 문단을 보면서 몇 번이든
                # TeachRequest(직접 교정 학습)를 보낼 수 있다. ParagraphDecision이
                # 와야만 다음 문단으로 넘어간다.
                t_wait = time.perf_counter()
                while True:
                    msg = self.to_worker.get()
                    if isinstance(msg, TeachRequest):
                        timing["review_wait"] += time.perf_counter() - t_wait
                        self._apply_teach(hwp, msg)
                        t_wait = time.perf_counter()
                        continue
                    decision: ParagraphDecision = msg
                    break
                timing["review_wait"] += time.perf_counter() - t_wait

                t0 = time.perf_counter()
                # 문장 단위(문체/마침표) 반영을 먼저 실행한다. 문장 전체를 원문
                # 그대로 찾아야 하는데, 단어 교정을 먼저 적용해버리면 그 문장
                # 안의 단어가 이미 바뀌어서 원문 문자열을 찾지 못해 조용히
                # 무시되는 문제가 있었다 (예: 문장 안 이름 오타가 단어 교정으로
                # 먼저 바뀌면, 그 문장을 대상으로 한 문체 교정이 매치 실패함).
                for original_sentence, new_sentence in decision.style_replacements.items():
                    if new_sentence and new_sentence.strip() != original_sentence.strip():
                        hwp.find_replace(
                            src=original_sentence,
                            dst=new_sentence,
                            regex=False,
                            direction="AllDoc",
                            MatchCase=1,
                        )
                        corrected += 1

                for s in suggestions:
                    if s.index in decision.accepted_indices:
                        # 실제 한/글 창에서 찾아 바꾸기를 실행 -> 반영되는 과정이
                        # 화면에 그대로 보인다.
                        hwp.find_replace(
                            src=s.original_word,
                            dst=s.corrected_word,
                            regex=False,
                            direction="AllDoc",
                            MatchCase=1,
                        )
                        corrected += 1
                timing["apply"] += time.perf_counter() - t0

                if decision.stop:
                    break

            ext = os.path.splitext(self.output_path)[1].lower()
            fmt = "HWPX" if ext == ".hwpx" else "HWP"
            t0 = time.perf_counter()
            hwp.save_as(self.output_path, format=fmt)
            timing["save"] = time.perf_counter() - t0
            timing["total"] = time.perf_counter() - t_run_start
            self.to_gui.put(("done", self.output_path, corrected, timing))
        except Exception as exc:  # noqa: BLE001
            self.to_gui.put(("error", str(exc)))
        finally:
            if hwp is not None:
                try:
                    hwp.quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()
