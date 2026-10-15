# 독립 도구 설치와 실행

## 설치 위치와 호출

저장소 루트의 `setup-local.cmd` / `./setup-local.sh`로 Python·Node·FFmpeg와 공개 npm 의존성을 로컬에 준비한다. 다른 저장소 접근은 필요하지 않다.

개인 스킬 설치는 저장소의 `.venv` Python으로 실행한다.

```powershell
.\.venv\Scripts\python.exe -X utf8 .\skills\product-text-inpaint\scripts\install.py
```

기본 위치는 `CODEX_HOME/skills/product-text-inpaint`, 변수가 없으면 `~/.codex/skills/product-text-inpaint`다. `--dest`로 전체 스킬 폴더 경로를 지정할 수 있다. 기존 설치를 덮어쓰지 않는다. 설치된 `runtime.json`에는 이 머신의 저장소 경로만 기록하며 Git에 포함하지 않는다. 새 Codex 세션을 연다.

다른 사진 폴더에서는 스킬의 `scripts/run.py`를 설치된 도구의 Python으로 호출한다. `$runtimeRoot`는 `runtime.json`의 `root`, `$skillRoot`는 설치된 스킬 폴더다. 상대 입력·출력 경로는 현재 사진 폴더 기준이다.

```powershell
& "$runtimeRoot\.venv\Scripts\python.exe" -X utf8 "$skillRoot\scripts\run.py" doctor
& "$runtimeRoot\.venv\Scripts\python.exe" -X utf8 "$skillRoot\scripts\run.py" cleanup --image original.jpg --box "100,100,600,800" --out output\clean --model supported-model-slug
```

`run.py`는 Git의 sh를 직접 호출해 Windows batch 런처의 쉼표 분리 문제를 피한다. `.cmd`를 직접 실행할 때는 `--%` 뒤 큰따옴표 좌표를 유지한다. `--%` 뒤에서는 PowerShell 변수가 확장되지 않는다.

## 원본 문자 추출

source는 실제 사진, base는 유지할 사진이다. 원본을 정리하면 같은 파일을 사용한다. 다른 크기·원근이면 같은 인쇄면의 TL, TR, BR, BL 모서리를 각각 지정한다.

```json
{
  "source_quad": [[100,100],[600,100],[600,900],[100,900]],
  "base_quad": [[200,150],[500,150],[500,750],[200,750]],
  "regions": [
    {"name":"title", "box":[210,170,490,280], "kernel":41,
     "noise_floor":0.10, "opaque_at":0.80, "exclude":[]}
  ]
}
```

숫자는 형식 예시다. EXIF 회전 후 직접 좌표를 정한다. 두 quad를 생략하면 source·base 크기가 같아야 한다. box는 base 좌표 `[x1,y1,x2,y2]`, 끝점은 배타적이다.

- `kernel`: 배경색 추정용 홀수 창. 가장 두꺼운 획보다 넓게 시작한다.
- `noise_floor`, `opaque_at`: 약한 배경 대비를 제외하고 잉크 중심을 불투명으로 추출하는 하한·상한. 값을 올려 가는 획을 없애지 않는다.
- `exclude`: 먼지·스크래치 등 제외할 base 사각형 목록.
- `mask`: 선택적 수동 PNG 마스크. JSON 기준 경로, 해당 box 크기, L 밝기 또는 RGBA A 채널이 알파다.

이 계산은 문자 추출이며 사진에 노이즈 제거 필터를 적용해 저장하지 않는다.

```powershell
.\text-clean.cmd layers prepare --source original.jpg --base studio.png --config regions.json --out out\prepare
```

`base-normalized.png`, `source-aligned.png`, `*-alpha.png`, `*-ink.png`, `*-preview.png`, `plan.json`을 남긴다. 마스크를 직접 검수한다. 준비 결과를 수정했으면 새 폴더에 다시 prepare한다.

## AI로 면 정리 후 잉크 복원

```powershell
.\text-clean.cmd restore --prepared out\prepare --out out\restored --model supported-model-slug --detail-level 1
```

각 박스를 직전 결과 위에 순서대로 편집하고 원본 잉크를 마지막에 합성한다. 출력은 `out/restored/final/final.png`, `verification.json`이다. 이전 글자가 지워졌는지, 최종 글자가 맞는지 100% 검수한다. 실패 시 새 폴더를 쓰고 성공한 배경은 재사용할 수 있다.

이미 지운 면이 있는 경우 AI를 호출하지 않는다.

```powershell
.\text-clean.cmd layers composite --prepared out\prepare --clean-base clean-base.png --out out\final
.\text-clean.cmd layers verify --prepared out\prepare --image out\final\final.png
```

잔여 글자나 잘못 포함한 어두운 사진 때문에 검사에 실패하면 배경이나 box를 고친다. 검증은 박스 밖과 불투명 잉크 내부 보존이며, 폰트·정렬·글자 내용·질감은 육안 판정이다.

## 사진·글자 자체의 AI 정리

```powershell
.\text-clean.cmd --% cleanup --image "original.jpg" --box "100,100,600,800" --out "out\clean" --model "supported-model-slug" --detail-level 1
.\text-clean.cmd cleanup --image original.jpg --out out\clean-whole --model supported-model-slug
```

box를 생략하면 전체 사진을 정리한다. AI가 해당 영역을 다시 그리므로 글자·제품 구조를 비교한다. 결과는 `clean-000.png`와 박스 밖 보존 검증이다. `--batch 1..8`, `--request`, `--dry-run`을 지원한다. dry-run은 실제 생성·품질 검증이 아니다.

macOS/Linux는 `./text-clean`, `.venv/bin/python`을 사용한다. `CODEX_IMAGEGEN_MODEL` 또는 `--model`에 현재 계정이 지원하는 slug를 지정한다. 2026-10-14 테스트에서는 `gpt-6.1-sol`로 성공했으며 현재 지원 여부는 각 계정에서 확인한다. 인증 만료 시 `codex login` 후 doctor로 확인한다. 전역 모델 설정은 변경하지 않는다.
