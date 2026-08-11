"""자동 검사기가 놓친 오류를 사람이 직접 알려주면 저장해두는 "학습 사전".

참조 폴더 안에 `_learned_corrections.json` 파일로 저장된다. 다음 실행부터는:
  1) 똑같은 표현이 다시 나오면 유사도 계산 없이 즉시(정확히) 잡아내고,
  2) 올바른 표현이 참조 사전 어휘에도 추가되어, 비슷한 다른 오탈자를 잡는 데도
     도움이 된다.

즉, 사람이 한 번 고쳐준 내용이 프로그램의 "지식"으로 누적된다.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime

_FILE_NAME = "_learned_corrections.json"


@dataclass
class LearnedCorrection:
    wrong: str
    correct: str
    reason: str
    added_at: str


def _path(reference_folder: str) -> str:
    return os.path.join(reference_folder, _FILE_NAME)


def load_learned_corrections(reference_folder: str) -> list:
    path = _path(reference_folder)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [LearnedCorrection(**item) for item in data]
    except Exception as exc:  # noqa: BLE001
        print(f"[학습 사전] '{path}' 읽기 실패: {exc}")
        return []


def add_learned_correction(reference_folder: str, wrong: str, correct: str, reason: str) -> None:
    wrong = wrong.strip()
    correct = correct.strip()
    if not wrong or not correct or wrong == correct:
        return

    os.makedirs(reference_folder, exist_ok=True)
    corrections = load_learned_corrections(reference_folder)
    # 같은 표현을 다시 가르치면 최신 내용으로 덮어쓴다.
    corrections = [c for c in corrections if c.wrong != wrong]
    corrections.append(
        LearnedCorrection(
            wrong=wrong, correct=correct, reason=reason.strip(),
            added_at=datetime.now().isoformat(timespec="seconds"),
        )
    )
    with open(_path(reference_folder), "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in corrections], f, ensure_ascii=False, indent=2)
