# tools/ — 실행 유틸리티 모음

이 폴더의 스크립트 사용법을 한곳에 정리한다. **새 툴을 추가하면 반드시 이 문서에 (무엇/언제/실행법) 항목을 추가**한다.

## 실행 규칙 (공통)

- Isaac API 를 쓰는 툴은 **번들 파이썬** `/isaac-sim/python.sh` 로 실행한다 (시스템 `python3` 없음).
- 장비(RealSense) 를 읽는 툴은 **Isaac 이 아니라** 카메라가 꽂힌 호스트/환경의 파이썬 + `pyrealsense2` 로 실행한다.
- 표의 "런타임" 열이 어느 파이썬으로 돌릴지 알려준다.

| 툴 | 하는 일 | 런타임 | Isaac 필요 |
|---|---|---|---|
| `setup_assets.py` | fresh PC 한 방 세팅: gitignore 된 에셋 전부 재생성 | `/isaac-sim/python.sh` | ○ (subprocess) |
| `fetch_isaac_assets.py` | DR 배경/재질 풀(바닥·하늘·환경USD) 로컬 다운로드 | `/isaac-sim/python.sh` | ○ |
| `import_cad.py` | CAD(STL/OBJ/FBX) → `assets/obj/<id>/mesh.usd` | `/isaac-sim/python.sh` | ○ |
| `read_realsense_intrinsics.py` | D435 실측 intrinsics → config `sensors[]` 블록 | 장비 파이썬 + pyrealsense2 | ✗ |
| `capture_realsense_depth.py` | D435 depth 캡처(노이즈 보정용, **선택**) | 장비 파이썬 + pyrealsense2 | ✗ |
| `fit_depth_noise.py` | 캡처 → `realsense_depth` 노이즈 파라미터 피팅(**선택**) | numpy 아무거나 | ✗ |
| `visualize.py` | 생성 데이터셋 위에 GT 오버레이(QA) | `/isaac-sim/python.sh` | ✗ |
| `fix_perms.sh` | root 로 실행돼 오염된 소유권 복구 | root 쉘 | ✗ |
| `run_gui_stream.sh` | 헤드리스 컨테이너에서 GUI WebRTC 스트리밍 | isaac-sim 유저 | ○ |

---

## ★ 카메라와 "완벽 데이터 vs 실센서 열화" (자주 묻는 것)

**기본값은 완벽(GT) 데이터다.** sensor `type` 기본값은 `ideal` 이고, 모든 예제 config 가 `ideal` 을 쓴다 →
depth 는 노이즈/구멍 없는 **완벽한 metric GT**. **아무 것도 캘리브레이션하지 않아도 지금 바로 생성이 된다.**

`capture_realsense_depth.py` 와 `fit_depth_noise.py` 는 **오직** sensor `type: realsense_depth` 를
골라서 "일부러 실제 D435 처럼 depth 를 열화" 시키고 싶을 때만 쓰는 **선택** 도구다.

| 원하는 것 | sensor type | 캘리브레이션 | depth 결과 |
|---|---|---|---|
| 완벽 GT depth (기본) | `ideal` | 불필요 | 노이즈 0, 구멍 0 |
| 실센서 흉내(값 대충) | `realsense_depth` | 안 채움 → **placeholder 기본값** 사용 | 열화되지만 D435 실측 아님 |
| 실센서 흉내(충실) | `realsense_depth` | capture→fit 로 채움 | D435 통계와 정합 |

> 요약: **안 채우면 `ideal` = 완벽 데이터.** 실센서 열화는 opt-in 이고, 충실하게 하려면 아래 워크플로우로 값을 채운다.

---

## setup_assets.py — fresh PC 한 방 세팅

`git clone` 직후, 커밋되지 않은(=gitignore) 에셋을 **원래 디렉토리에 전부 재생성**한다:
바닥 텍스처 → `assets/textures/ground/`, HDRI 하늘 → `assets/env/hdri/`, 환경 USD → `assets/env/usd/<name>/`,
CAD→mesh → `assets/obj/<id>/mesh.usd`. (`fetch_isaac_assets.py`·`import_cad.py` 를 순서대로 subprocess 호출.)

```bash
/isaac-sim/python.sh tools/setup_assets.py            # floors + skies + objects (기본)
/isaac-sim/python.sh tools/setup_assets.py --all      # + 환경 USD (대용량: office~680MB 등)
/isaac-sim/python.sh tools/setup_assets.py --steps floors,skies
/isaac-sim/python.sh tools/setup_assets.py --envs warehouse,office --steps envs
/isaac-sim/python.sh tools/setup_assets.py --force    # 이미 있어도 재생성
/isaac-sim/python.sh tools/setup_assets.py --dry-run  # 실행할 명령만 출력(다운로드 X)
```

- **Idempotent**: 이미 채워진 dir 은 건너뜀. `--force` 로 강제. 환경 USD 는 대용량이라 opt-in(`--all`/`--envs`).
- **새 오브젝트 추가**: 스크립트 상단 `OBJECT_IMPORTS` 리스트에 `{obj_id, cad, units, up_axis}` 한 줄 추가 →
  다음 부트스트랩부터 자동 포함.

## fetch_isaac_assets.py — DR 에셋 로컬화 (개별)

setup_assets 가 내부에서 부르지만 개별로도 쓴다. 카테고리별 독립 실행.

```bash
/isaac-sim/python.sh tools/fetch_isaac_assets.py --all                      # 바닥+하늘 (envs 제외)
/isaac-sim/python.sh tools/fetch_isaac_assets.py --envs simple_room,office  # 환경 USD (opt-in)
/isaac-sim/python.sh tools/fetch_isaac_assets.py --floors --dry-run         # 대상 URL 만 출력
```
정확한 클라우드 URL 은 `assets/ASSET_SOURCES.md` 에 자동 기록(섹션별 병합).

## import_cad.py — CAD → mesh.usd

```bash
/isaac-sim/python.sh tools/import_cad.py <input.stl> --obj-id obj_000 \
    --input-units mm --up-axis Z [--no-center] [--load-materials]
```
입력 단위→metres 스케일 + bbox 중심정렬, self-contained `mesh.usd` 생성. obj 명 하드코딩 금지(원칙2).

---

## D435 depth 노이즈 보정 워크플로우 (선택 — `realsense_depth` 쓸 때만)

sim GT depth 를 실제 D435 처럼 열화시키되, 노이즈 값을 **추측이 아니라 실측**으로 채우는 4단계.
평평한 벽은 참깊이를 정확히 알 수 있어(평면방정식) 측정 depth 의 이탈 = 센서 오차가 된다.

```
[1] 캡처   capture_realsense_depth.py  — 벽을 여러 알려진 거리에서 촬영
[2] 분석   fit_depth_noise.py          — 평면 피팅 → bias/노이즈/구멍 산출
[3] 반영   출력된 config 블록을 sensors[] 에 붙여넣기
[4] 검증   같은 장면 sim 렌더 → 열화 depth 오차 히스토그램 vs 실측 비교, 안 맞으면 [1] 반복
```

### 1) intrinsics 읽기 (장비쪽)
```bash
pip install pyrealsense2 numpy         # 카메라 꽂힌 환경
python3 tools/read_realsense_intrinsics.py --stream aligned_depth_to_color \
        --width 1280 --height 720 --json calibration/d435.json
```
→ `sensors[]` 블록(fx/fy/cx/cy/resolution/near_clip_m) 출력.

### 2) depth 캡처 (장비쪽, 거리마다 1회)
```bash
python3 tools/capture_realsense_depth.py --type plane   --distance 0.40 --frames 30
python3 tools/capture_realsense_depth.py --type plane   --distance 0.75 --frames 30
python3 tools/capture_realsense_depth.py --type plane   --distance 1.50 --frames 30
python3 tools/capture_realsense_depth.py --type surface --distance 0.60 --frames 30  # 구멍(어둡/반사)
```
→ `calibration/d435/<type>_z<dist>/{depth.npy, meta.json, color.png}` 저장.

### 3) 피팅 (아무 numpy 파이썬, Isaac 도 됨)
```bash
/isaac-sim/python.sh tools/fit_depth_noise.py --name d435 [--roi 0.4] [--csv calibration/d435/fit.csv]
```
→ 거리별 표 + 피팅된 `bias_mm`·`noise_quadratic`·`hole_fraction` + 붙여넣을 config 블록 출력.
거리에 따라 bias 가 커지면(>3mm/m) scale 항 필요를 경고(현재 모델은 상수 bias — CLAUDE.md 로드맵).

### 4) 합쳐서 sensor 엔트리 완성
1) 의 intrinsics 블록 + 3) 의 노이즈 블록을 합쳐 `config/<run>.yaml` 의 `sensors[]` 에 넣는다:
```yaml
sensors:
  - name: d435
    type: realsense_depth
    resolution: [1280, 720]
    intrinsics: {fx: <read>, fy: <read>, cx: <read>, cy: <read>}
    near_clip_m: 0.105
    bias_mm: <fit>
    noise_quadratic: <fit>
    edge_dropout: true
    hole_fraction: <fit>
    noise_seed: 0
```

---

## visualize.py — 데이터셋 QA 오버레이

```bash
/isaac-sim/python.sh tools/visualize.py datasets/<run> [--max 20] [--axis-len 0.1]
```
`generic` 포맷 데이터셋의 rgb 위에 bbox_2d/bbox_3d/keypoints/pose 축을 그려 `<dataset>/qa/` 에 저장.
Isaac 불필요(numpy+Pillow).

## fix_perms.sh / run_gui_stream.sh
- `fix_perms.sh` — root 실행으로 오염된 소유권 복구. 상세: `DEPENDENCIES.md §5`.
- `run_gui_stream.sh` — 헤드리스 컨테이너에서 GUI 를 WebRTC 로 스트리밍(X 서버 없이). isaac-sim 유저로 실행.
