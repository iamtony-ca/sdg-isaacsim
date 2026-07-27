# tools/ — 실행 유틸리티 모음

이 폴더 스크립트의 **무엇을/왜/어디서/어떻게**를 파일별로 정리한다.
**새 툴을 추가하면 반드시 이 문서에 항목을 추가**한다(프로젝트 규칙).

## 실행 규칙 (공통)

- **Isaac API 를 쓰는 툴은 번들 파이썬 `/isaac-sim/python.sh`** 로 실행한다. `omni.*` / `pxr` 은 Kit 런타임이
  세팅해 주므로 시스템 파이썬에서는 import 되지 않는다.
- **Isaac 이 필요 없는 툴**(`visualize.py`, `fit_depth_noise.py`)은 시스템 `python3`(3.12 — numpy·cv2·PIL 포함)
  으로도 돌아가고, SimulationApp 기동이 없어 훨씬 빠르다.
- **장비(RealSense)를 읽는 툴**은 Isaac 이 아니라 **카메라가 꽂힌 호스트/환경의 파이썬 + `pyrealsense2`** 로
  실행한다.
- 아래 표의 "런타임" 열이 어느 파이썬으로 돌릴지 알려준다. 모든 명령은 워크스페이스 루트(`sdg_ws/`)에서 실행.

| 툴 | 하는 일 | 런타임 | Isaac 필요 |
|---|---|---|---|
| `setup_assets.py` | fresh clone 한 방 세팅: gitignore 된 에셋 전부 재생성 | `/isaac-sim/python.sh` | ○ (subprocess) |
| `fetch_isaac_assets.py` | DR 풀(바닥 텍스처·HDRI 하늘·환경 USD) 로컬 다운로드 | `/isaac-sim/python.sh` | ○ |
| `import_cad.py` | **CAD(STEP 등) → `assets/obj/<id>/mesh.usd`** | `/isaac-sim/python.sh` | ○ |
| `visualize.py` | 생성 데이터셋 위에 GT 오버레이(QA) | `python3` 또는 `python.sh` | ✗ |
| `read_realsense_intrinsics.py` | 실측 intrinsics → config `sensors[]` 블록 | 장비 파이썬 + pyrealsense2 | ✗ |
| `capture_realsense_depth.py` | 실 depth 캡처(노이즈 보정용, **선택**) | 장비 파이썬 + pyrealsense2 | ✗ |
| `fit_depth_noise.py` | 캡처 → `realsense_depth` 노이즈 파라미터 피팅(**선택**) | `python3`(numpy) | ✗ |
| `fix_perms.sh` | root 실행으로 오염된 소유권 복구 | root 쉘 | ✗ |
| `run_gui_stream.sh` | 헤드리스 컨테이너에서 GUI 를 WebRTC 로 스트리밍 | isaac-sim 유저 | ○ |

**전형적인 순서**: `setup_assets.py`(1회) → [새 객체가 생기면 `import_cad.py`] → 생성(`sdg/run_sdg.py`) →
`visualize.py`(QA). RealSense 3종은 **실센서 열화를 쓸 때만** 필요한 선택 경로다(아래 ★ 참조).

---

## ★ 카메라: "완벽 데이터 vs 실센서 열화" (자주 묻는 것)

**기본값은 완벽(GT) 데이터다.** sensor `type` 기본값은 `ideal` 이고 모든 예제 config 가 `ideal` 을 쓴다 →
depth 는 노이즈/구멍 없는 **완벽한 metric GT**. **아무 것도 캘리브레이션하지 않아도 지금 바로 생성이 된다.**

`capture_realsense_depth.py` 와 `fit_depth_noise.py` 는 **오직** sensor `type: realsense_depth` 로
"일부러 실제 D435 처럼 depth 를 열화" 시킬 때만 쓰는 **선택** 도구다.

| 원하는 것 | sensor type | 캘리브레이션 | depth 결과 |
|---|---|---|---|
| 완벽 GT depth (기본) | `ideal` | 불필요 | 노이즈 0, 구멍 0 |
| 실센서 흉내(값 대충) | `realsense_depth` | 안 채움 → **placeholder 기본값** | 열화되지만 실측 아님 |
| 실센서 흉내(충실) | `realsense_depth` | capture→fit 로 채움 | 실측 통계와 정합 |

---

# setup_assets.py — fresh clone 한 방 세팅

**무엇**: 이 repo 가 생성 시점에 필요하지만 **커밋하지 않는**(전부 gitignore — 서드파티 바이너리이거나
재생성 가능) 에셋을 **config 가 이미 참조하는 바로 그 디렉토리에** 되살린다. 실행 후에는 오프라인 config
(`config/env_offline.yaml`, `config/dr_demo.yaml`)가 네트워크 없이 돈다.

**복원 대상 4스텝**:

| step | 무엇 | 목적지 | 위임 |
|---|---|---|---|
| `floors` | 사실적 바닥 텍스처 | `assets/textures/ground/` | `fetch_isaac_assets --floors` |
| `skies` | HDRI 하늘 env map | `assets/env/hdri/` | `fetch_isaac_assets --skies` |
| `envs` | 환경 USD 배경 (**대용량·opt-in**) | `assets/env/usd/<name>/` | `fetch_isaac_assets --envs` |
| `objects` | **CAD → mesh.usd** | `assets/obj/<obj_id>/mesh.usd` | `import_cad`(git 추적된 CAD 에서) |

**왜 subprocess 오케스트레이터인가**: `fetch_isaac_assets.py` 와 `import_cad.py` 는 각각 SimulationApp 을
띄우는데, 그 `.close()` 가 `os._exit` 를 호출해 **프로세스 전체를 죽인다**(CLAUDE.md 함정 1). 따라서 두 툴은
한 프로세스를 공유할 수 없다 → 이 드라이버가 각각을 별도 subprocess 로 순서대로 돌린다. 드라이버 자신은
Isaac 을 import 하지 않는 순수 파이썬이다.

```bash
/isaac-sim/python.sh tools/setup_assets.py                      # floors + skies + objects (기본)
/isaac-sim/python.sh tools/setup_assets.py --all                # + envs (대용량: office ~680MB)
/isaac-sim/python.sh tools/setup_assets.py --steps floors,skies
/isaac-sim/python.sh tools/setup_assets.py --envs warehouse,office   # --steps 없이도 envs 포함
/isaac-sim/python.sh tools/setup_assets.py --steps objects --force   # mesh.usd 강제 재생성
/isaac-sim/python.sh tools/setup_assets.py --dry-run            # 실행할 명령만 출력(다운로드 X)
```

| 옵션 | 기본 | 의미 |
|---|---|---|
| `--steps a,b` | `floors,skies,objects` | 실행할 스텝. 유효값 `floors,skies,envs,objects` |
| `--all` | — | envs 포함 전 스텝 |
| `--envs a,b` | `simple_room,office` | 로컬화할 env 프리셋. **주면 envs 스텝이 자동 포함**된다 |
| `--limit N` | 48 | 바닥 텍스처 최대 개수(fetch 로 전달) |
| `--force` | — | 이미 채워져 있어도 재실행 |
| `--dry-run` | — | 계획만 출력 |

- **Idempotent**: 목적지 dir 이 이미 채워져 있으면 스텝을 건너뛴다(`--force` 로 강제). envs 는 용량이 커서
  기본 스텝에서 빠져 있다.
- **실패 처리**: 스텝별 실패를 모아 마지막에 `DONE with FAILURES: [...]` 를 찍고 **exit 1**. CAD 소스가 없으면
  `object(<id>):no-cad` 로 표시된다.
- **`ISAAC_PYTHON`** 환경변수로 번들 파이썬 경로를 덮어쓸 수 있다(기본 `/isaac-sim/python.sh`).

**새 객체 추가** — 스크립트 상단 `OBJECT_IMPORTS` 에 한 줄 추가하면 다음 부트스트랩부터 자동 포함된다:

```python
{"obj_id": "obj_001",
 "cad": "assets/cad/<폴더>/part.stp",   # git 추적되는 CAD 소스 (repo 상대경로)
 "units": "auto",                        # auto|mm|cm|m|in
 "up_axis": "Z",
 "tess_lod": 3},                         # (선택) B-rep 테셀레이션 밀도, 기본 2
```

---

# fetch_isaac_assets.py — DR 에셋 로컬화

**무엇**: SDG 랜덤화기는 매 프레임 **ground 재질**(카메라가 내려다보는 면 = 사실상 보이는 "배경")과
**dome HDRI** 를 교체한다. 그런데 Isaac Sim 은 그 라이브러리를 **디스크에 동봉하지 않는다** — NVIDIA 클라우드
에셋 서버에 있다. 오프라인 생성/배포를 위해 엄선된 세트를 **한 번** repo 로 내려받고, 이후 config 는 로컬
디렉토리만 참조한다.

| 카테고리 | 소스(클라우드) | 목적지 |
|---|---|---|
| 바닥 텍스처 | `{assets_root}/NVIDIA/Materials/{Base,vMaterials_2}/...` (열거) | `assets/textures/ground/` |
| HDRI 하늘 | `{assets_root}/NVIDIA/Assets/Skies/<Category>/*.hdr` (엄선 15) | `assets/env/hdri/` |
| 환경 USD | `{assets_root}/Isaac/Environments/<Name>/...` (**opt-in**) | `assets/env/usd/<name>/` |

```bash
/isaac-sim/python.sh tools/fetch_isaac_assets.py --all                      # 바닥+하늘 (envs 제외)
/isaac-sim/python.sh tools/fetch_isaac_assets.py --floors --limit 24
/isaac-sim/python.sh tools/fetch_isaac_assets.py --skies
/isaac-sim/python.sh tools/fetch_isaac_assets.py --envs simple_room,office  # 환경 USD (opt-in)
/isaac-sim/python.sh tools/fetch_isaac_assets.py --floors --dry-run         # 대상 URL 만 출력
```

- 플래그를 하나도 안 주면 `--all` 과 같다. `--dry-run` 은 열거·URL 출력만 하고 내려받지 않는다.
- **환경 USD 는 단일 파일 복사로는 깨진다** → `omni.kit.usd.collect.Collector` 로 stage + 머티리얼 + 텍스처 +
  props 의존성까지 수집하고 경로를 리맵한다. 그래서 오래 걸리고 용량이 크다(office ~680MB, simple_room ~120MB).
- **자기검증 열거**: 실제 존재하는 diffuse/BaseColor 이미지만 수집한다(normal/roughness/orm/썸네일 제외).
  파일명을 추측하지 않는다.
- **출처 기록**: 정확한 클라우드 URL 이 `assets/ASSET_SOURCES.md`(git 추적)에 자동 기록된다. `<!--sec:KEY-->`
  마커로 카테고리별 병합이라 `--envs` 만 다시 돌려도 floors/skies 기록이 지워지지 않는다.
- 소스 정의(무엇을 받을지)의 단일 출처는 `sdg/assets.py` 상단이다.
- 내려받은 바이너리는 gitignore — `mesh.usd` 와 같은 재현 모델(툴로 재생성).

---

# import_cad.py — CAD → `assets/obj/<obj_id>/mesh.usd`

**무엇**: 기구팀이 준 CAD 파일 하나를 SDG 에셋 규약으로 바꾼다. **CAD 는 기본적으로 `.stp`(STEP)로 들어온다고
가정**한다. 결과 `mesh.usd` 는 **self-contained · metres(metersPerUnit=1) · bbox 중심정렬**이라 씬 빌더가 그대로
참조한다. 객체 이름은 어디에도 박지 않는다 — `obj_id` 로만 참조(원칙2).

```bash
/isaac-sim/python.sh tools/import_cad.py "assets/cad/<폴더>/part.stp" --obj-id obj_001
```

| 옵션 | 기본 | 의미 |
|---|---|---|
| `--obj-id` | (필수) | 목적지 `assets/obj/<obj_id>/` |
| `--input-units` | `auto` | `auto\|mm\|cm\|m\|in`. auto = 변환된 stage 의 `metersPerUnit` 사용 |
| `--up-axis` | `Z` | 결과 stage 의 up-axis 메타(**지오메트리를 회전하지는 않음**) |
| `--tess-lod` | `2` | B-rep 테셀레이션 밀도. 곡면이 각지면 3~4 로 (삼각형·용량 ↑) |
| `--no-center` | — | bbox 중심정렬 하지 않음 |
| `--load-materials` | — | 소스 재질을 살림(기본은 무시 — SDG 는 materials 랜덤화기가 칠한다) |

### 백엔드는 확장자로 자동 선택된다 (6.0.1 설치본 대조 확인)

| 입력 | 백엔드 | 비고 |
|---|---|---|
| `.stp` `.step` `.igs` `.iges` `.CATPart` `.sldprt` `.ipt` `.x_t` `.jt` `.prt` … (**B-rep**) | `omni.kit.converter.hoops_core` (HOOPS Exchange) | 곡면을 삼각형으로 **테셀레이션** → `--tess-lod` |
| `.stl` `.obj` `.fbx` `.gltf` `.glb` (**이미 삼각형 메시**) | `omni.kit.asset_converter` | |

지원 목록의 단일 출처는 `omni/kit/converter/hoops_core/impl/filters.py`.
⚠️ `omni.kit.asset_converter` 는 B-rep 을 **거부**한다(`UNSUPPORTED_IMPORT_FORMAT`) — 그래서 라우팅이 필요하다.

### 단위 — 여기서 틀리면 데이터셋 전체가 틀린 스케일로 나온다

`--input-units auto`(기본)는 변환된 stage 의 `metersPerUnit` 을 읽되, 두 포맷의 성격이 정반대라 구분한다:

- **B-rep 은 저작 단위를 파일에 갖고 있다** → auto 가 정확(실측: 이 STEP 은 inch 저작 → `0.0254`).
- **STL/OBJ 는 단위가 없다** → Kit 이 기본값 `0.01` 을 찍는데 이건 정보가 아니다. auto 가 이를 **거부**하고
  경고와 함께 mm 로 폴백한다(실측: 같은 파트의 STL raw 수치 180.975 = mm).

**항상 출력 마지막의 `final size (m)` 를 실물 도면과 대조**한다. 어긋나면 배수로 나타난다 —
25.4×(inch↔mm), 1000×(m↔mm), 10×(cm↔mm) → `--input-units` 를 명시해 재변환.

```
[import_cad] converting ... -> ..._raw.usd  [hoops (B-rep CAD)]
[import_cad] --input-units auto -> metersPerUnit=0.0254 from the converted stage
[import_cad] final size (m): [0.181, 0.1762, 0.1529]  (expected ~[0.181, 0.1762, 0.1529])
```

### 왜 STL 보다 STEP 이 나은가 (같은 파트 실측)

| | `.stl` | `.stp` (tessLOD 2) |
|---|---|---|
| points | 41,400 | **14,320** |
| triangles | 13,800 | **16,881** |
| mesh.usd | 995 KB | **402 KB** |
| bbox·정렬·up·단위 | — | **완전히 동일**(드롭인 교체) |

`41,400 = 13,800 × 3` → **STL 은 정점 공유가 없는 triangle soup** 이라 법선이 면 단위로 각진다
(`annotators.normals` 품질에 직접 영향). HOOPS 경로는 인덱스 메시라 부드러운 법선이 가능하고 밀도를
`--tess-lod` 로 올릴 수 있다. 비용은 변환 시간뿐(STEP 수 분 vs STL 1분 내외, fresh clone 1회).
→ **B-rep 과 파생 STL 이 둘 다 있으면 B-rep 을 쓴다.**

### 그 밖의 주의

- **좌표계**: 이 툴은 지오메트리를 회전하지 않는다(up-axis 메타만). `objects[].origin: bottom` 과
  `rotation: yaw` 는 로컬 AABB 의 Z-최소를 "바닥"으로 보므로 **CAD 저작 축이 Z-up** 이어야 진짜 바닥을 맞힌다.
- **실패 시 exit 2 + stderr**. (예전엔 fast-shutdown 의 `os._exit(0)` 탓에 실패해도 exit 0 이라 조용히
  빈 폴더만 남았다.)
- **재현성**: `mesh.usd` 는 gitignore, CAD 소스는 git 추적. fresh clone 자동 재생성을 위해
  `setup_assets.py::OBJECT_IMPORTS` 에 등록할 것.
- 새 객체 등록 전체 워크플로우(변환 → 크기검증 → config → 첫 렌더 → QA)는 **`quick_start.md §0-1`**.

---

# visualize.py — 데이터셋 QA 오버레이

**무엇**: `generic` 포맷 데이터셋(`rgb/` + `meta/`)을 읽어 GT 를 이미지 위에 그린다. **숫자로만 보면 놓치는
좌표계 실수를 눈으로 잡는** 용도 — 대량 생성 전 필수 체크포인트.

| 그리는 것 | 색 |
|---|---|
| `bbox_2d` | 초록 사각형 |
| `bbox_3d` | 청록 wireframe (meta 의 `corners_2d`) + occlusion 라벨 |
| `keypoints_2d` | 노란 점(보임) / 흐린 점(뒤·화면 밖) |
| `pose_cam` | 물체 좌표축 (X 빨강, Y 초록, Z 파랑) |

```bash
python3 tools/visualize.py datasets/example --max 20          # Isaac 불필요 (numpy + Pillow)
python3 tools/visualize.py datasets/dr_demo --axis-len 0.05
```

| 옵션 | 기본 | 의미 |
|---|---|---|
| `--max N` | 전체 | 처리할 프레임 수 제한 |
| `--out DIR` | `qa` | 출력 서브디렉토리 이름 |
| `--axis-len M` | `0.1` | pose 축 길이(metres) |

→ `<dataset>/qa/000000.png …`

- **통과 기준**: 박스가 물체를 정확히 감싸고, pose 축이 물체 원점(보통 바닥 중앙)에서 뻗는다.
- **BOP/COCO/YOLO 는 대상이 아니다**(generic `meta/` 를 읽는다). 그 포맷들은 `quick_start.md` 각 절의
  "통과 기준" JSON 값으로 검증한다.
- pose 축 투영은 USD 카메라 규약(−Z 전방, +Y 위) + meta intrinsics 를 쓴다. bbox_3d·keypoints 는 meta 에
  이미 투영돼 있다.

---

# RealSense 3종 — depth 노이즈 보정 워크플로우 (선택)

`realsense_depth` 센서를 **실측 통계에 맞춰** 쓸 때만 필요하다. sim GT depth 를 열화시키되 그 파라미터를
**추측이 아니라 측정**으로 채운다. 평평한 벽은 참깊이를 정확히 알 수 있으므로(평면방정식) 측정 depth 의
이탈이 곧 센서 오차다.

```
[1] 캡처   capture_realsense_depth.py  — 벽을 여러 알려진 거리에서 촬영   (장비쪽)
[2] 분석   fit_depth_noise.py          — 평면 피팅 → bias/노이즈/구멍     (numpy 아무데나)
[3] 반영   출력된 config 블록을 sensors[] 에 붙여넣기
[4] 검증   같은 장면 sim 렌더 → 열화 depth 오차 히스토그램 vs 실측, 안 맞으면 [1] 반복
```

## read_realsense_intrinsics.py — 실측 intrinsics → config 블록

**무엇**: `sensors/ideal.py` 는 `{fx, fy, cx, cy}` 를 주면 실카메라와 **픽셀 단위로 일치**시킬 수 있다.
이 값은 **개체마다 다르므로**(같은 모델의 두 대가 다름) 하드코딩하지 말고 실제 장비에서 읽는다.

```bash
pip install pyrealsense2                                   # 카메라 꽂힌 환경
python3 tools/read_realsense_intrinsics.py                                  # color 1280x720
python3 tools/read_realsense_intrinsics.py --stream aligned_depth_to_color  # ★ 권장
python3 tools/read_realsense_intrinsics.py --serial 123456789012 --json calibration/d435.json
```

| `--stream` | 의미 |
|---|---|
| `color` | RGB 이미저 (RGB 와 depth 를 따로 쓸 때) |
| `depth` | depth 이미저 자체 프레임(좌측 IR 시점, color 와 정렬 안 됨) |
| `aligned_depth_to_color` | color 프레임으로 리샘플된 depth — **단일 카메라 모델에 권장**(rgb·depth 가 intrinsics 공유) |

기타: `--width/--height/--fps`, `--name`(출력 블록의 sensor 이름), `--json PATH`(전 스트림 raw 덤프).
→ `sensors[]` 블록(fx/fy/cx/cy/resolution/near_clip_m) 출력.

> **왜곡**: RealSense color/depth 스트림은 이미 near-pinhole 로 rectify 돼 있다(Brown-Conrady 계수 ≈ 0).
> 우리 카메라 모델은 순수 pinhole 이라 `coeffs` 를 무시한다 — 스크립트가 값을 찍어주니 무시해도 되는지
> 확인하라. 유의미한 계수가 나오고 그게 필요하면 collector 레벨 변경이 필요하다.

## capture_realsense_depth.py — 실 depth 캡처 (거리마다 1회)

```bash
python3 tools/capture_realsense_depth.py --type plane   --distance 0.40 --frames 30
python3 tools/capture_realsense_depth.py --type plane   --distance 0.75 --frames 30
python3 tools/capture_realsense_depth.py --type plane   --distance 1.50 --frames 30
python3 tools/capture_realsense_depth.py --type surface --distance 0.60 --frames 30  # 구멍(어둡/반사)
```

- `--type plane` = 평평한 매트 벽을 **측정한 수직 거리**에서. 작업 범위를 아우르는 ~5개 거리 권장.
- `--type surface` = 어둡거나 반짝이거나 투명한 면 → dropout(구멍) 비율 측정용.
- 기타: `--frames`(스택 프레임 수, 기본 30), `--warmup`(기본 30), `--width/--height/--fps`,
  `--serial`(여러 대일 때), `--name`(기본 `d435` → `calibration/<name>/`).

→ `calibration/<name>/<type>_z<dist>/` 에 저장:

| 파일 | 내용 |
|---|---|
| `meta.json` | intrinsics, depth_scale, known_distance_m, type, resolution, serial |
| `depth.npy` | float32 `(N,H,W)` **metres** (0 = 무효/무반환) |
| `color.png` | 마지막 컬러 프레임(육안 참조용) |

## fit_depth_noise.py — 캡처 → 파라미터 피팅

**무엇**: 각 `plane_z<d>/` 캡처를 점군으로 역투영해 중앙 ROI 에 평면을 피팅하고 측정한다:
`bias(z)` = 측정 중앙 depth − 알려진 거리(계통 오차), `sigma(z)` = 프레임 내 점-평면 잔차 표준편차(랜덤 노이즈).
거리들에 걸쳐 `noise_quadratic k` (`sigma = k·z²`, stereo depth 특성)와 bias 를 피팅한다.
`surface_z<d>/` 캡처는 `hole_fraction` 에 기여한다.

```bash
python3 tools/fit_depth_noise.py --name d435
python3 tools/fit_depth_noise.py --name d435 --roi 0.4 --csv calibration/d435/fit.csv
```

거리별 표 + 피팅된 `bias_mm`·`noise_quadratic`·`hole_fraction` + **붙여넣을 config 블록**을 출력한다.
거리에 따라 bias 가 커지면(>3mm/m) **scale 항 필요**를 경고한다(현재 모델은 상수 bias — 로드맵 항목).

## [4] 합쳐서 sensor 엔트리 완성

intrinsics 블록 + 노이즈 블록을 합쳐 `config/<run>.yaml` 의 `sensors[]` 에 넣는다:

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

# fix_perms.sh — 소유권 복구 (root 전용)

**무엇/왜**: 이 컨테이너에는 `sudo` 가 없고 일반 사용자(`isaac-sim`)는 자기 그룹뿐이다. 그래서 **root 로 Isaac
Sim 을 한 번이라도 돌리면** 공유 캐시(`/isaac-sim/kit/{cache,logs,data}`, `.nv`, `.cache`,
`.nvidia-omniverse`, `exts/omni.pip.*`)와 이 워크스페이스에 root 소유 파일이 남아 이후 `runapp.sh` /
`runheadless.sh` 가 제대로 못 뜬다. 이 스크립트가 그 소유권을 런타임 사용자로 되돌린다.

```bash
sh tools/fix_perms.sh --check    # 검사만 (아무 사용자나 가능) — 몇 개가 root 소유인지 보고
sh tools/fix_perms.sh            # 복구 (root 쉘에서)
docker exec -u root <container> sh /isaac-sim/volume/sdg_ws/tools/fix_perms.sh   # 호스트에서
```

- **root 로 실행해야 한다**(`chown` 필요). isaac-sim 계정에서는 고칠 수 없다(sudo 없음) → `--check` 만 가능.
- 런타임 사용자는 `SDG_RUNTIME_USER`(기본 `isaac-sim`), Isaac 루트는 `ISAAC_SIM_ROOT`(기본 `/isaac-sim`)로
  덮어쓸 수 있다. 상세 배경: `DEPENDENCIES.md`.

# run_gui_stream.sh — 헤드리스 컨테이너에서 GUI 보기

**무엇/왜**: 이 컨테이너에는 X 서버가 없어 `~/runapp.sh`(네이티브 GUI)가 창을 못 연다. 정상 경로는 NVIDIA 의
**WebRTC 라이브스트림**(`~/runheadless.sh`) — GPU 에서 헤드리스로 렌더해 클라이언트로 스트리밍한다.
이 래퍼는 광고할 IP 를 잡아 넣고 접속 정보를 출력해 준다.

```bash
tools/run_gui_stream.sh                          # isaac-sim 유저로 실행
ISAACSIM_HOST=<ip> tools/run_gui_stream.sh       # 광고 IP 수동 지정
```

접속: "Isaac Sim WebRTC Streaming Client" 로 출력된 IP 에 연결. **포트 49100/tcp(시그널), 47998/udp(미디어)**
가 도달 가능해야 한다(이 컨테이너는 host networking 이라 호스트 IP 로 바로 열린다). Ctrl+C 로 중지.
