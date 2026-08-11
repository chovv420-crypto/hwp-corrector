"""수정 과정을 실시간으로 보여주는 팝업 창.

실제 아래한글 편집창(worker.py가 연다) 옆에 이 창을 띄워두면,
문단별로 어떤 단어가 왜 바뀌는지 확인하면서 승인/거절할 수 있다.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk

from .worker import ParagraphDecision, ParagraphTask, TeachRequest

COLOR_PENDING = "#fff3b0"
COLOR_ACCEPTED = "#b7e4c7"
COLOR_REJECTED = "#f4a3a3"


class CorrectionApp(tk.Tk):
    def __init__(self, to_gui: "queue.Queue", to_worker: "queue.Queue"):
        super().__init__()
        self.title("한글 문서 오탈자·문맥 교정 도우미")
        self.geometry("780x580")

        self.to_gui = to_gui
        self.to_worker = to_worker
        self.current_task: "ParagraphTask | None" = None
        self.word_states: dict = {}
        self.selected_index = None

        self._build_widgets()
        self.after(150, self._poll_queue)

    # ---------------- 위젯 구성 ----------------
    def _build_widgets(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        self.progress_label = ttk.Label(top, text="문서를 여는 중입니다...", font=("맑은 고딕", 11, "bold"))
        self.progress_label.pack(anchor="w")
        self.progress_bar = ttk.Progressbar(top, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(4, 0))

        mid = ttk.LabelFrame(self, text="현재 문단  (노란 배경 = 교정 제안 단어, 클릭해서 이유 확인)", padding=8)
        mid.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.text = tk.Text(mid, wrap="word", font=("맑은 고딕", 12), height=10, cursor="arrow")
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")

        reason = ttk.LabelFrame(self, text="선택한 단어 정보", padding=8)
        reason.pack(fill="x", padx=10, pady=(0, 8))
        self.reason_label = ttk.Label(
            reason, text="교정 제안 단어(노란 배경)를 클릭하면 왜 바꾸자는 건지 여기에 표시됩니다.",
            wraplength=730, justify="left",
        )
        self.reason_label.pack(anchor="w", fill="x")

        btns = ttk.Frame(reason)
        btns.pack(anchor="w", pady=(6, 0))
        self.accept_btn = ttk.Button(
            btns, text="이 단어 승인", state="disabled",
            command=lambda: self._set_word_state("accepted"),
        )
        self.accept_btn.pack(side="left", padx=(0, 6))
        self.reject_btn = ttk.Button(
            btns, text="이 단어 거절", state="disabled",
            command=lambda: self._set_word_state("rejected"),
        )
        self.reject_btn.pack(side="left")

        style_frame = ttk.LabelFrame(
            self, text="문장 확인 필요 (문체 불일치 · 마침표 누락 — 직접 고쳐서 반영 가능)", padding=8
        )
        style_frame.pack(fill="x", padx=10, pady=(0, 8))
        self.style_rows_frame = ttk.Frame(style_frame)
        self.style_rows_frame.pack(fill="x")
        self.style_empty_label = ttk.Label(style_frame, text="이 문단에는 확인할 문장이 없습니다.")
        self.style_empty_label.pack(anchor="w")
        self.style_rows: list = []

        teach_frame = ttk.LabelFrame(
            self,
            text="자동으로 못 잡은 오류를 발견했나요? 직접 알려주면 학습해서 다음부터 바로 잡아냅니다",
            padding=8,
        )
        teach_frame.pack(fill="x", padx=10, pady=(0, 8))

        row1 = ttk.Frame(teach_frame)
        row1.pack(fill="x")
        ttk.Label(row1, text="틀린 표현", width=10).pack(side="left")
        self.teach_wrong_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.teach_wrong_var).pack(side="left", fill="x", expand=True)

        row2 = ttk.Frame(teach_frame)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="올바른 표현", width=10).pack(side="left")
        self.teach_correct_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.teach_correct_var).pack(side="left", fill="x", expand=True)

        row3 = ttk.Frame(teach_frame)
        row3.pack(fill="x", pady=(4, 0))
        ttk.Label(row3, text="이유(선택)", width=10).pack(side="left")
        self.teach_reason_var = tk.StringVar()
        ttk.Entry(row3, textvariable=self.teach_reason_var).pack(side="left", fill="x", expand=True)

        row4 = ttk.Frame(teach_frame)
        row4.pack(fill="x", pady=(6, 0))
        ttk.Button(row4, text="학습하고 지금 문서에 반영", command=self._submit_teach).pack(side="left")
        self.teach_status_label = ttk.Label(row4, text="", foreground="#2b7a2b")
        self.teach_status_label.pack(side="left", padx=(10, 0))

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        ttk.Button(
            bottom, text="전체 승인 후 다음 문단 →",
            command=lambda: self._next_paragraph(accept_all=True),
        ).pack(side="left")
        ttk.Button(
            bottom, text="다음 문단 → (선택한 단어만 반영)",
            command=lambda: self._next_paragraph(),
        ).pack(side="left", padx=6)
        ttk.Button(bottom, text="여기서 중단하고 저장", command=self._stop_and_save).pack(side="right")

    # ---------------- 워커 -> GUI 큐 폴링 ----------------
    def _poll_queue(self) -> None:
        try:
            while True:
                item = self.to_gui.get_nowait()
                if isinstance(item, ParagraphTask):
                    self._render_task(item)
                elif isinstance(item, tuple) and item and item[0] == "taught":
                    _, wrong, correct = item
                    self.teach_status_label.configure(text=f"학습 완료: '{wrong}' → '{correct}'")
                elif isinstance(item, tuple) and item and item[0] == "done":
                    _, output_path, count, timing = item
                    messagebox.showinfo("완료", self._format_done_message(output_path, count, timing))
                    self.destroy()
                elif isinstance(item, tuple) and item and item[0] == "error":
                    messagebox.showerror("오류", f"작업 중 오류가 발생했습니다:\n{item[1]}")
                    self.destroy()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll_queue)

    @staticmethod
    def _format_done_message(output_path: str, count: int, timing: dict) -> str:
        def fmt(sec: float) -> str:
            return f"{sec:.1f}초"

        return (
            f"교정이 완료되었습니다.\n"
            f"반영된 문단 수: {count}개\n"
            f"저장 위치: {output_path}\n\n"
            f"[소요 시간]\n"
            f"문서 스캔: {fmt(timing.get('scan', 0))}\n"
            f"오탈자/조사/문체 계산(자동): {fmt(timing.get('suggest', 0))}\n"
            f"실제 반영(찾아 바꾸기): {fmt(timing.get('apply', 0))}\n"
            f"저장: {fmt(timing.get('save', 0))}\n"
            f"검토 대기(사람이 승인/거절하는 데 걸린 시간): {fmt(timing.get('review_wait', 0))}\n"
            f"전체: {fmt(timing.get('total', 0))}"
        )

    # ---------------- 문단 렌더링 ----------------
    def _render_task(self, task: ParagraphTask) -> None:
        self.current_task = task
        self.word_states = {s.index: "pending" for s in task.suggestions}
        self.selected_index = None
        self.accept_btn.configure(state="disabled")
        self.reject_btn.configure(state="disabled")
        self.reason_label.configure(text="교정 제안 단어(노란 배경)를 클릭하면 왜 바꾸자는 건지 여기에 표시됩니다.")

        self.progress_bar.configure(maximum=task.total, value=task.index + 1)
        self.progress_label.configure(
            text=(
                f"문단 {task.index + 1}/{task.total} 확인 중 — "
                f"오탈자·문법 의심 {len(task.suggestions)}건, 문장 확인(문체/마침표) {len(task.style_issues)}건"
            )
        )

        suggestion_by_idx = {s.index: s for s in task.suggestions}

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for i, word in enumerate(task.words):
            tag = f"w{i}"
            self.text.insert("end", word, (tag,) if i in suggestion_by_idx else ())
            self.text.insert("end", " ")
            if i in suggestion_by_idx:
                self.text.tag_configure(tag, background=COLOR_PENDING, underline=True)
                self.text.tag_bind(tag, "<Button-1>", lambda e, idx=i: self._select_word(idx))
        self.text.configure(state="disabled")

        self._render_style_issues(task.style_issues)

    def _render_style_issues(self, issues: list) -> None:
        for child in self.style_rows_frame.winfo_children():
            child.destroy()
        self.style_rows = []

        if not issues:
            self.style_empty_label.pack(anchor="w")
            return
        self.style_empty_label.pack_forget()

        for issue in issues:
            row = ttk.Frame(self.style_rows_frame)
            row.pack(fill="x", pady=(0, 8))
            ttk.Label(row, text=issue.reason, wraplength=720, justify="left").pack(anchor="w")

            entry_row = ttk.Frame(row)
            entry_row.pack(fill="x", pady=(3, 0))
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(entry_row, text="이 문장 반영", variable=var).pack(side="left")
            prefill = getattr(issue, "suggested_sentence", issue.sentence)
            entry_var = tk.StringVar(value=prefill)
            ttk.Entry(entry_row, textvariable=entry_var).pack(side="left", fill="x", expand=True, padx=(6, 0))

            self.style_rows.append({"issue": issue, "var": var, "entry_var": entry_var})

    def _select_word(self, idx: int) -> None:
        self.selected_index = idx
        suggestion = next(s for s in self.current_task.suggestions if s.index == idx)
        self.reason_label.configure(
            text=f"'{suggestion.original_word}'  →  '{suggestion.corrected_word}'\n\n{suggestion.reason}"
        )
        self.accept_btn.configure(state="normal")
        self.reject_btn.configure(state="normal")

    def _set_word_state(self, state: str) -> None:
        if self.selected_index is None:
            return
        self.word_states[self.selected_index] = state
        tag = f"w{self.selected_index}"
        color = COLOR_ACCEPTED if state == "accepted" else COLOR_REJECTED
        self.text.tag_configure(tag, background=color)

    def _submit_teach(self) -> None:
        wrong = self.teach_wrong_var.get().strip()
        correct = self.teach_correct_var.get().strip()
        reason = self.teach_reason_var.get().strip()
        if not wrong or not correct:
            self.teach_status_label.configure(text="틀린 표현과 올바른 표현을 모두 입력하세요.", foreground="#a33")
            return
        if wrong == correct:
            self.teach_status_label.configure(text="틀린 표현과 올바른 표현이 같습니다.", foreground="#a33")
            return
        self.to_worker.put(TeachRequest(wrong=wrong, correct=correct, reason=reason))
        self.teach_status_label.configure(text="반영 중...", foreground="#2b7a2b")
        self.teach_wrong_var.set("")
        self.teach_correct_var.set("")
        self.teach_reason_var.set("")

    def _collect_style_replacements(self) -> dict:
        return {
            row["issue"].sentence: row["entry_var"].get()
            for row in self.style_rows
            if row["var"].get()
        }

    # ---------------- 다음 문단으로 진행 ----------------
    def _next_paragraph(self, accept_all: bool = False) -> None:
        if self.current_task is None:
            return
        if accept_all:
            accepted = {s.index for s in self.current_task.suggestions}
        else:
            accepted = {idx for idx, st in self.word_states.items() if st == "accepted"}
        style_replacements = self._collect_style_replacements()
        self.to_worker.put(
            ParagraphDecision(accepted_indices=accepted, style_replacements=style_replacements, stop=False)
        )
        self._show_waiting()

    def _stop_and_save(self) -> None:
        if self.current_task is None:
            return
        accepted = {idx for idx, st in self.word_states.items() if st == "accepted"}
        style_replacements = self._collect_style_replacements()
        self.to_worker.put(
            ParagraphDecision(accepted_indices=accepted, style_replacements=style_replacements, stop=True)
        )
        self._show_waiting()

    def _show_waiting(self) -> None:
        self.current_task = None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._render_style_issues([])
        self.progress_label.configure(text="다음 교정 제안을 찾는 중입니다...")
