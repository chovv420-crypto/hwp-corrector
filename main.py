"""한글 문서 오탈자·문맥 교정 도우미 (오프라인, 내 PC 참조 폴더 기반).

실행 방법 (Windows + 한컴오피스 설치 필수):
    python main.py 검사할문서.hwpx
    python main.py 검사할문서.hwpx -r ./my_knowledge_base -o 결과.hwpx
    python main.py                      <- 인자 없이 실행하면 파일 선택 창이 뜬다
                                            (exe를 더블클릭했을 때도 이 경로를 탄다)

참조 폴더(my_knowledge_base)에 과거 보고서, 용어집(.hwpx/.hwp/.txt)을
넣어두면 그 문서들에서 자주 쓰인 표현을 기준으로 오탈자/문맥을 검사한다.
인터넷 연결이 필요 없다.
"""
from __future__ import annotations

import argparse
import os
import queue
import sys


def _default_output_path(input_path: str) -> str:
    base, ext = os.path.splitext(input_path)
    return f"{base}_교정본{ext}"


def _pause_and_exit(interactive: bool, code: int = 1) -> None:
    """더블클릭으로 실행됐을 때(콘솔이 바로 닫혀버리는 것을 막기 위해) 메시지를 읽을 시간을 준다."""
    if interactive:
        try:
            input("\n아무 키나 누르면 창이 닫힙니다...")
        except EOFError:
            pass
    sys.exit(code)


def _pick_input_file() -> str:
    """CLI 인자 없이 실행됐을 때(예: exe 더블클릭) 파일 선택 창을 띄운다."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="검사할 한글 문서를 선택하세요",
        filetypes=[("한글 문서", "*.hwpx *.hwp"), ("모든 파일", "*.*")],
    )
    root.destroy()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="한글 문서 오탈자/문맥 교정 도우미 (오프라인, 내 PC 참조 폴더 기반)"
    )
    parser.add_argument("input", nargs="?", help="검사할 hwp/hwpx 파일 경로 (생략하면 파일 선택 창이 뜸)")
    parser.add_argument("-o", "--output", help="저장할 파일 경로 (기본: 원본명_교정본.확장자)")
    parser.add_argument(
        "-r", "--reference", default="./my_knowledge_base",
        help="참조 문서 폴더 경로 (기본: ./my_knowledge_base)",
    )
    args = parser.parse_args()

    interactive = args.input is None

    if sys.platform != "win32":
        print(
            "이 프로그램은 실제 '한글' 프로그램을 화면에 띄워 원격 조종하므로,\n"
            "Windows + 한컴오피스(아래한글)가 설치된 PC에서만 실행할 수 있습니다."
        )
        _pause_and_exit(interactive)

    input_path = args.input
    if input_path is None:
        input_path = _pick_input_file()
        if not input_path:
            print("파일을 선택하지 않아 종료합니다.")
            _pause_and_exit(interactive, code=0)

    if not os.path.exists(input_path):
        print(f"오류: 입력 파일을 찾을 수 없습니다 -> {input_path}")
        _pause_and_exit(interactive)

    output = args.output or _default_output_path(input_path)

    # Windows 전용 모듈은 여기서(실행 시점에만) import한다.
    from hwp_corrector.gui import CorrectionApp
    from hwp_corrector.reference_db import build_reference_db
    from hwp_corrector.worker import CorrectionWorker

    print(f"[1/2] 참조 폴더 '{args.reference}'에서 조직 고유 용어를 학습합니다...")
    ref_db = build_reference_db(
        args.reference,
        progress_cb=lambda done, total, name: print(f"  ({done}/{total}) {name}"),
    )
    print(f" -> {len(ref_db.word_freq)}개의 고유 용어 확보")
    if ref_db.exact_corrections:
        print(f" -> 이전에 학습된 교정 사례 {len(ref_db.exact_corrections)}건 적용됨")
    if ref_db.dominant_style:
        from hwp_corrector.style_checker import STYLE_LABELS

        print(f" -> 참조 문서 기준 문체: {STYLE_LABELS[ref_db.dominant_style]}")
    if not ref_db.word_freq:
        print(
            "경고: 참조 폴더가 비어 있습니다.\n"
            f"'{args.reference}' 폴더에 과거 보고서(.hwpx/.hwp/.txt)를 넣고 다시 실행하세요."
        )
        _pause_and_exit(interactive)

    print("[2/2] 아래한글 문서를 열고 검사를 시작합니다. 잠시 후 한글 창과 교정 도우미 창이 함께 뜹니다...")

    to_gui: "queue.Queue" = queue.Queue()
    to_worker: "queue.Queue" = queue.Queue()

    worker = CorrectionWorker(input_path, output, ref_db, args.reference, to_gui, to_worker)
    worker.start()

    app = CorrectionApp(to_gui, to_worker)
    app.mainloop()


if __name__ == "__main__":
    main()
