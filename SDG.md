# SDG.md — 범용 Synthetic Data Generation 환경 설계 (Isaac Sim 6.0.1)

> 설계/계획 문서. 현재 상태·실행은 `README.md`, 세션 컨텍스트·원칙은 `CLAUDE.md`.
> **목표: 특정 태스크/객체에 종속되지 않는, config·plugin 구동 SDG 프레임워크.**

---

## 0. 목표 / 비목표

**목표**
- Isaac Sim 6.0.1 Replicator 위에서 **RGB·depth·segmentation·bbox·normal·keypoint·6D pose** 등 임의의
  annotation 조합을 **config 한 장**으로 생성.
- 객체·씬·randomization·센서·출력포맷을 **플러그인**으로 갈아끼우는 확장 구조.
- 재현성: headless·시드 고정·파라미터화. GUI 수작업 의존 제거(마이그레이션·재실행 내구성).

**비목표 (지금)**
- 특정 다운스트림 모델(6D pose 등) 학습 그 자체 — SDG 는 *데이터*를 만들 뿐. 학습은 소비자 프로젝트/별도 venv.
- 특정 객체(예: 카세트)에 최적화 — 그건 하나의 config 인스턴스일 뿐.

---

## 1. 아키텍처 — config → 5개 확장 축 → 프레임 루프

```
config/example.yaml
   │  (sdg/config.py 로 RunConfig 파싱·검증)
   ▼
run_sdg.py  ── app.py(SimulationApp 6.0.1) ── 프레임 루프:
   ┌─────────────────────────────────────────────────────────────┐
   │ 1) scene      : 배경/ground/obj(들) spawn, semantic label 부여 │
   │ 2) randomizers: 조명/재질/pose/카메라/distractor/occluder 변형   │
   │ 3) sensors    : 카메라 render product (ideal + 옵션 depth 열화)  │
   │ 4) annotators : RGB/depth/seg/bbox/normal/keypoint/pose GT 수집  │
   │ 5) writers    : 지정 포맷으로 디스크 기록 (generic / BOP / ...)  │
   └─────────────────────────────────────────────────────────────┘
```

### 확장 축 (plugin registry: name → class)
| 축 | 인터페이스(`base.py`) | 역할 | 확장 방법 |
|---|---|---|---|
| **objects** | (config `objects[]`) | `obj_id` 로 자산 로드·물리·semantic | `assets/obj/<obj_id>/` + config 항목 |
| **randomizers** | `Randomizer` | 프레임별 변형 등록/적용 | 새 randomizer 클래스 + config `randomizers[]` |
| **sensors** | `CameraModel` | render product·intrinsics·(옵션)열화 | 새 센서 모델 클래스 |
| **annotators** | `Annotator` | GT 채널 수집(정규화된 dict) | config `annotators[]` 로 on/off |
| **writers** | `Writer` | 포맷 기록 | 새 writer(generic/BOP/COCO/…) |

새 태스크 = **새 config**(어떤 objects·randomizers·annotators·writer 조합) — 원칙적으로 코드 무수정.

---

## 2. config 스키마 (초안, YAML)

```yaml
run:
  name: example
  num_frames: 50
  seed: 42
  headless: true
  output_dir: datasets/${run.name}

scene:
  builder: default            # sdg/scene 레지스트리 키
  background: none            # none | ground_plane | warehouse | <usd path>
  ground_plane: true

objects:                      # 객체는 obj_id 로만 참조 (특정명 하드코딩 X)
  - obj_id: obj_000           # assets/obj/obj_000/ 에서 로드
    count: 1
    physics: {collider: true, gravity: false, mass: null}   # gravity:false 면 pose 가 배치를 지배
    semantic: {class: obj_000}
    # origin: bottom|top|center | {face: bottom} | [x,y,z] | {keypoint: i}   # (옵션) pose_6d 원점
    #   재정의 + 실제 배치 원점까지 지배(파묻힘 방지). face/bottom 등은 스폰 prim 의 bbox+stage up-axis 로
    #   자동 계산(객체 무종속). 관측 표면 정렬용; consumer CAD 도 동일 원점. 기본=asset 원점.
    # part-level mask 는 assets/obj/<obj_id>/parts.json 로 서브프림에 semantic class 부여(전용 asset 파일).

randomizers:                  # 순서대로 매 프레임 적용
  # lighting: hdri(dir/list)+hdri_rotate 로 image-based lighting/배경 DR (dome env map + 회전)
  - {type: lighting,   intensity: [500, 3000], count: [1, 3], kinds: [dome, distant, rect]}
  # materials: target objects|ground|all. textures(dir)+texture_prob+texture_scale 로 텍스처 DR.
  #   텍스처 이미지는 UV 필요 → UV 없는 STL 은 project_uvw(world planar)라 ground/평면엔 깨끗,
  #   오브젝트 수직면엔 smear. 그래서 오브젝트는 색/roughness/metallic 랜덤화가 실질 경로.
  - {type: materials,  target: all, roughness: [0.1, 0.9], metallic: [0.0, 0.6], base_color: hsv_jitter}
  # pose rotation: none | yaw(=z_only, 직립 유지·origin:bottom 과 짝) | uniform_euler | uniform_so3
  - {type: pose,       target: objects, position: {x: [-0.2,0.2], y: [-0.2,0.2], z: [0,0.1]}, rotation: uniform_so3}
  - {type: camera,     mode: look_at, distance: [0.6, 2.0], elevation_deg: [20, 80], azimuth_deg: [-180,180]}
  # occluder: 카메라-타깃 시선 위 배치로 부분 가림 보장(distractor 와 달리). MUST come after pose+camera.
  #   pool=제네릭 도형(prim:cube/sphere/cylinder/cone/capsule)|obj_id|usd. 실제 가림은 visib_fract(GT),
  #   occlusion_frac 은 바이어스. target_region:<part class> 로 특정 부위(예 flange) 부분가림.
  - {type: occluder,   pool: [prim:cube, prim:cylinder], count: [0, 2], occlusion_frac: [0.15, 0.45]}
  - {type: distractors, pool: [], count: [0, 3]}     # 유사/이질 객체 clutter (비면 없음; 가림 보장 X)

sensors:
  - name: cam0
    type: ideal                 # ideal | realsense_depth(옵션 열화)
    resolution: [1280, 720]
    # intrinsics 3-mode (택1): 실카메라 정합엔 {fx,fy,cx,cy}(정사각·off-centre 지원),
    #   또는 {focal_mm} / {hfov_deg}(둘 다 정사각 픽셀). calibration/ 실측치를 fx/fy/cx/cy 로 그대로.
    intrinsics: {fx: 952.2, fy: 952.2, cx: 640.0, cy: 360.0}
    # realsense_depth 사용 시: {model: d435, bias_mm: 0, noise: quadratic, holes: true, calib: calibration/...}

annotators:                     # 필요한 GT 만 켠다
  rgb: true
  depth: true                   # ideal metric depth (GT)
  semantic_segmentation: true
  instance_segmentation: true
  bbox_2d: true
  bbox_3d: false
  normals: false
  keypoints: []                 # obj_id 별 3D keypoint 정의가 있으면 2D 투영+3D 기록
  pose_6d: true                 # camera-object 상대 변환

writer:
  format: generic               # generic(폴더구조) | bop | coco | yolo | ...
  depth_png_bits: 16
  mask_binary: true
```

> config 는 **선언적**이고 객체 무종속. 6D pose 예시가 필요하면 이 config 에서 `pose_6d/depth/mask` 켜고
> writer 를 `bop` 으로 바꾸는 정도로 표현된다(코드 아님).

---

## 2.1 랜덤화 인벤토리 (현재 상태 — 단일 출처)

**등록된 randomizer = 7종** (`sdg/randomizers/__init__.py`; `registry.available("randomizer")` 로 확인).
전부 구현·검증 완료. config `randomizers[]` 에 **나열된 순서대로 매 프레임 적용**되므로 순서가 의미를 가진다
(특히 occluder 는 camera·pose **뒤**여야 그 프레임의 카메라/타깃 위치를 읽어 배치한다).

| # | `type` | 무엇을 랜덤화 | 주요 config 키 | 서브모드 / 비고 |
|---|---|---|---|---|
| 1 | **lighting** | dome 앰비언트 밝기 + blackbody 색온도 + overhead fixture(방향광·실제 그림자) + HDRI 환경맵 | `intensity`, `color_temperature`, `hdri`, `hdri_rotate`, `fixtures{kinds:[rect,distant],count,intensity,color_temperature,distance,elevation_deg,size}` | **HDRI 15종**(로컬 dir 오프라인/`isaac_skies` 온라인), fixture kinds **2**(rect·distant) |
| 2 | **materials** | 대상 표면의 색·거칠기·금속성·텍스처 | `target`, `roughness`, `metallic`, `base_color`, `textures`, `texture_prob`, `texture_scale` | `target: objects\|ground\|all`. `base_color: hsv_jitter\|none`. UV 없는 mesh 는 project_uvw(평면엔 OK, 수직면 smear) |
| 3 | **background** | 환경 USD 씬 전환(warehouse/office/simple_room/hospital/grid…) | `pool`, `interval` | **9 프리셋**(grid×3·warehouse×3·simple_room·office·hospital). 로컬 경로=오프라인/프리셋명=온라인. pool 전부 pre-reference 후 interval 마다 하나만 visible |
| 4 | **pose** | 객체 위치 + 회전 (+ origin-aware 배치) | `target`, `position{x,y,z}`, `rotation`, `yaw_deg` | `rotation: none\|yaw(=z_only)\|uniform_euler\|uniform_so3`. `objects[].origin: bottom` 과 짝이면 바닥 안착 |
| 5 | **camera** | 시점 (look-at 반구/구면) | `mode: look_at`, `distance`, `elevation_deg`, `azimuth_deg` | obj centroid 를 바라보며 구면좌표 샘플. obj별 valid-view 제약은 로드맵(§CONSUMER §4-D) |
| 6 | **distractors** | 방해 물체(clutter) 스폰·산포 | `pool`, `count`, `extents`, `semantic_class` | pool 빈=no-op. 씬 아무 데나 scatter → **가림 비보장**. pool 에 obj_id/usd |
| 7 | **occluder** | **부분 가림 보장** (카메라-타깃 시선 위 배치) | `pool`, `count`, `occlusion_frac`, `target_region`, `depth_range`, `jitter`, `semantic_class` | pool=`prim:{cube,sphere,cylinder,cone,capsule}`(**도형 5종**)\|obj_id\|usd. 실제 가림=**visib_fract GT**(occlusion_frac 은 바이어스). `target_region:<part class>` 로 특정 부위(parts.json) |

**에셋 풀 카탈로그 개수** (asset-pool 랜덤화가 고를 수 있는 "종류 수"; 정의 = `sdg/assets.py`):

| 풀 | 카탈로그 개수 | 세부 | 로컬화 위치 |
|---|---|---|---|
| HDRI 하늘 (lighting `hdri`) | **15종 / 5 카테고리** | Clear 5·Cloudy 2·Evening 1·Indoor 5·Night 2 | `assets/env/hdri/` (fetch, gitignore) |
| 환경 USD 배경 (background `pool`) | **9 프리셋** | grid×3·warehouse×3·simple_room·office·hospital | `assets/env/usd/<name>/` (opt-in, 대용량) |
| 바닥 텍스처 (materials `target:ground`) | **동적 ~50종** | 8 카탈로그 dir 열거(wood/stone/masonry/carpet/concrete/gravel…), diffuse 만; curated fallback 6 | `assets/textures/ground/` (fetch, gitignore) |
| occluder 제네릭 도형 | **5종** | cube·sphere·cylinder·cone·capsule | 엔진 내장 프리미티브 |
| pose 회전 모드 | **4** | none·yaw·uniform_euler·uniform_so3 | — |
| lighting fixture kinds | **2** | rect·distant (+ dome ambient) | 엔진 라이트 |

> **개수 주의**: HDRI·환경·바닥 풀은 **다운로드(gitignore)** → **실제 고르는 개수 = 로컬 dir 에 받은 파일 수**
> (오프라인) 또는 카탈로그 전체(온라인, 네트워크). `distractors`·`occluder` 의 obj/usd pool 과 materials 의
> 사용자 `textures` dir 는 **config 에 넣은 개수만큼**. 색/거칠기/금속성·조명 intensity·색온도·카메라 각도·
> pose 위치는 **개수가 아니라 연속 범위**(무한). 현재 개수를 확인하려면:
> `ls assets/env/hdri | wc -l`, `ls -d assets/env/usd/*/`, `ls assets/textures/ground | wc -l`.
>
> **에셋을 더 받아 랜덤화에 적용하는 절차**(폴더 glob=바닥·HDRI 는 drop-in / 명시 리스트=배경·distractor·
> occluder 는 pool 에 추가 / 환경은 collect 필요)는 `DEPENDENCIES.md §3-2` 참조.

**랜덤화는 아니지만 매 프레임 확률적 변형** (참고):
- **`realsense_depth` 센서** (`sensors[].type`, randomizer 아님·sensor 플러그인): bias + 거리²노이즈 + edge dropout
  + speckle hole 로 depth 를 실센서처럼 열화. 기본은 `ideal`(무열화 GT). 값은 `calibration/` 실측으로(임의값 금지).

**엔진 backing 요약** (전부 우리 플러그인이 config→per-frame Set 하지만 밑단 차이):
- 손잡이만 돌림(엔진 속성): materials·pose·camera·distractors·occluder·background(USD/머티리얼/피직스 속성).
- 엔진 라이트/렌더 기능: lighting(dome/rect/distant light).
- 미구현(추가 가능): 재질 투명/유리(OmniGlass, §CONSUMER §4-J 보류), 객체 스케일 지터, 물리 낙하/적재,
  RGB 센서 열화(노이즈/블러/노출 — 자체 후처리), 카메라 왜곡(rectify 하면 불요), 대기/볼류메트릭(저우선).

> 이 표가 "현재 랜덤화 정도"의 **단일 출처**다. 새 randomizer 를 추가하면 여기 행을 추가할 것.
> 개념 개요(학습용)는 `introduction.md §5`, 상세 구현·함정·검증은 `CLAUDE.md` 각 항목.

---

## 3. 출력 포맷

- **generic (MVP, 지금)**: obj/태스크 무종속 폴더 구조.
  ```
  datasets/<run>/
    rgb/000000.png ...
    depth/000000.png        # 16-bit mm
    semantic/000000.png     # class id map
    instance/000000.png     # instance id map
    meta/000000.json        # intrinsics, camera pose, per-object 6D pose, bbox, keypoints
    dataset.json            # 전역: 카메라·클래스·obj_id 목록·config 스냅샷
  ```
- **BOP (구현 완료, `writer.format: bop`)**: 표준 6D pose 벤치마크 포맷.
  ```
  datasets/<run>/
    camera.json  obj_id_map.json  bop_info.json
    train_pbr/000000/
      scene_camera.json  scene_gt.json  scene_gt_info.json
      rgb/  depth/(uint16 mm)  mask/  mask_visib/
  ```
  포즈 = model→camera(OpenCV frame, mm). 필요 annotator: rgb·depth·instance_segmentation·pose_6d.
  `annotators.amodal: true` 시 오브젝트별 격리 렌더로 **amodal mask**(`mask/`) + 실제 `visib_fract` 기록
  (없으면 mask==mask_visib). CAD `models/*.ply` 는 별도 제공.
- **COCO (구현 완료, `format: coco`)**: `images/` + `annotations/instances_<split>.json`
  (images·categories[1-based]·annotations[bbox·area·iscrowd·segmentation polygon]).
- **YOLO (구현 완료, `format: yolo`)**: `images/<split>/` + `labels/<split>/*.txt`(정규화 `cls xc yc w h`,
  seg 옵션) + `data.yaml`(names·nc). class 0-based.
  > COCO/YOLO 의 mask→bbox/폴리곤 추출은 `sdg/writers/_shapes.py`(cv2). instance_segmentation 켜면
  > 마스크·segmentation 까지, 아니면 bbox_2d 로 박스만.

---

## 4. 옵션 preset — real-sensor 열화 (구현 완료, 필요할 때만)
sim depth 는 기본 **ideal(GT)**. 실센서(예: RealSense D435)를 모사해야 하면 `sensors[].type: realsense_depth`
로 **열화 레이어**를 켠다 (`sdg/sensors/realsense_depth.py`, `CameraModel.postprocess_depth` 훅):
전역 bias + 거리²비례 Gaussian 노이즈(σ=`noise_quadratic`·z²) + 경계 dropout(depth gradient) + 저반사
speckle hole(`hole_fraction`). `noise_seed` 로 재현. 파라미터 config 예:
```yaml
sensors:
  - {name: cam0, type: realsense_depth, resolution: [1280,720], intrinsics: {hfov_deg: 69},
     bias_mm: 5.0, noise_quadratic: 0.003, edge_dropout: true, edge_grad_thresh_m: 0.05,
     edge_dilate_px: 2, hole_fraction: 0.01, noise_seed: 0}
```
> ⚠️ 기본값은 **예시일 뿐 캘리브레이션 아님**. 실제 디바이스 재현엔 실측 GT-vs-sensor(`calibration/`)로
> 파라미터를 맞춰야 함(임의값 금지). core 아닌 **선택 plugin**.

**QA 시각화**: `tools/visualize.py <dataset>` (generic 포맷) → rgb 위에 bbox_2d/bbox_3d wireframe/
keypoints/pose 축을 그려 `<dataset>/qa/` 에 저장. Isaac 불필요(번들 python 으로 실행).

---

## 5. 로드맵

| 단계 | 내용 | 검증 |
|---|---|---|
| **S0 스캐폴딩** | 폴더·문서·config/registry/writer 골격(순수 파이썬) | ✅ |
| **S1 6.0.1 API** | Replicator/센서 API 델타 조사, `app`·`scene`·`sensors`·`annotators`·`run_sdg` 구현 | ✅ smoke 렌더 검증 |
| **S2 MVP** | obj 1개 + 기본 DR + RGB/depth/seg/pose + generic writer | ✅ obj_000(웨이퍼 카세트 CAD→USD) example.yaml 렌더·라벨·6D pose·QA 검증 |
| **S3 확장** | randomizer/센서/writer 포맷 추가, distractor·occluder·keypoint·bbox3d | ✅ materials·distractors·**occluder**·keypoint·bbox_3d·**BOP·COCO·YOLO writer** 완료 |
| **S4 preset** | real-sensor 열화, amodal mask, QA 시각화 | ✅ realsense_depth·amodal·`tools/visualize.py` 완료 |

**진행**: S1~S4 전부 구현·검증 완료. 프레임워크는 대량 생성 준비 상태 — 남은 것은 *생성/확장*
(대량 생성, part-level·keypoints 정의, stereo pair 출력 등). 세부 상태·다음 작업은 `CLAUDE.md` "현재 상태" 참조.

---

## 6. 함정 / 주의
- **6.0.1 API 추측 금지** — 설치본 예제와 대조 후 구현.
- **GXF/NITROS**: 무거운 GXF 노드(있다면) 컨테이너 분리 이슈는 Isaac 릴리스 노트 확인.
- **VRAM(32GB)**: 고해상도·다수 annotator·다수 카메라 동시 시 모니터.
- **재현성**: 시드·config 스냅샷을 출력에 함께 저장(`dataset.json`).
- **확장성 규율**: 특정 객체/태스크 로직이 core 로 새지 않게 — 항상 plugin/config 로.
