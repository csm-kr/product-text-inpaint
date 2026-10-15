# 실제 제품 이미지 샘플

완료한 제품 이미지 테스트의 입력·AI 정리 결과·원본 잉크 복원 결과입니다.

| 파일 | 용도 |
|---|---|
| `source.png` | 실제 원본의 EXIF 회전 후 RGB 픽셀. 3024×4032, 리사이즈 없음 |
| `base.png` | 입력으로 사용한 기존 흰 배경 제품컷. 1086×1448 |
| `clean-base.png` | 상·하단 글자를 지운 실제 AI 인페인팅 결과 |
| `final.png` | 원본의 상단 문구·하단 브랜드를 복원한 결과 |
| `regions-studio.json` | 실제 대응 모서리·문자 영역·추출 설정 |
| `regions-original.json` | source와 base가 같은 원본 구도 유지 테스트 좌표 |
| `verification-studio.json` | 포함한 파일로 재실행한 복원 검증 |
| `verification-original.json` | 완료한 원본 구도 유지 테스트 검증 |

이미지에는 EXIF·GPS·계정 정보를 넣지 않았으며 검증 기록의 개인 절대 경로를 제거했습니다. 가운데 인쇄 사진·작은 글자는 원본 복원 대상이 아니며 케이스·블록 역시 제공한 AI base의 결과입니다.

## AI 사용량 없이 재현

setup을 완료한 저장소 루트에서 실행합니다. 출력 폴더는 새 이름을 사용합니다.

```powershell
.\text-clean.cmd layers prepare --source examples\product-text\source.png --base examples\product-text\base.png --config examples\product-text\regions-studio.json --out out\sample-prepare
.\text-clean.cmd layers composite --prepared out\sample-prepare --clean-base examples\product-text\clean-base.png --out out\sample-final
.\text-clean.cmd layers verify --prepared out\sample-prepare --image out\sample-final\final.png
```

macOS/Linux:

```sh
./text-clean layers prepare --source examples/product-text/source.png --base examples/product-text/base.png --config examples/product-text/regions-studio.json --out out/sample-prepare
./text-clean layers composite --prepared out/sample-prepare --clean-base examples/product-text/clean-base.png --out out/sample-final
./text-clean layers verify --prepared out/sample-prepare --image out/sample-final/final.png
```

이 실행은 이미 만든 AI 결과로 로컬 추출·합성·검증을 재현합니다. 원격 생성 자체를 새로 검증하는 실행은 아닙니다. 원근·크기를 변환한 잉크는 변환 후 준비한 레이어와 비교합니다. 문자 가장자리·내용·폰트·잔여 질감은 시각 검수가 필요합니다.

이미지와 제품 상표의 권리는 코드 라이선스에 포함되지 않습니다. 자신의 이미지 작업에는 사용 권리가 있는 사진을 입력하세요. 설치·AI 실행 방법은 [README](../../README.md)와 [워크플로](../../skills/product-text-inpaint/references/workflow.md)에 있습니다.
