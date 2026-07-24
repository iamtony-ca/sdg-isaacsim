# introduction.md — SDG 개념 정리 (입문/학습용)

> 이 문서는 **개념 학습용**입니다. "Isaac Sim SDG로 대체 뭘 만드는 건가?"를 처음 배우는 사람이 읽는 글.
> 실행 방법은 [`quick_start.md`](quick_start.md), 설계·확장 축은 [`SDG.md`](SDG.md), 세션 컨텍스트·원칙은
> [`CLAUDE.md`](CLAUDE.md)를 보세요. 여기서는 **원리와 "실제 결과가 어느 파일에 있는지"**만 다룹니다.

---

## 1. SDG란 한 줄로

**가상 3D 씬을 렌더링해서, 그 씬의 정답(Ground Truth) 라벨을 공짜로·자동으로 뽑아내는 것.**

실사진은 사람이 일일이 라벨링해야 하지만, 시뮬레이터는 물체가 *어디에 있고 · 무슨 픽셀이고 · 거리가 얼만지*를
이미 알고 있습니다. 그래서 렌더 한 장을 뽑을 때 **완벽한 라벨이 같이** 나옵니다. 이게 SDG의 전부입니다.

생성물은 언제나 이 형태입니다:

```
포토리얼한 렌더 이미지  +  그 이미지에 대한 여러 종류의 정답 라벨(채널)
        ↓ 여기에
프레임마다 조명/재질/배경/카메라를 무작위화(domain randomization)
        ↓ 그 결과
실사 없이도 실제 환경에 일반화되는 모델을 학습시킬 수천~수만 장의 데이터셋
```

---

## 2. 두 층위로 나눠 보면 안 헷갈린다

SDG를 배울 때 반드시 구분해야 하는 두 가지:

- **(A) 데이터의 "종류" = annotator (GT 채널)**
  씬에서 *무엇을* 뽑을 것인가. RGB·depth·segmentation·bbox·6D pose … 켜고 끄는 on/off 플래그.
  우리 프레임워크에서는 config의 `annotators.*`로 켭니다. 수집 코드는 `sdg/annotators/collector.py` 하나.

- **(B) 데이터의 "포맷" = writer**
  뽑은 채널들을 *어떤 표준 구조로* 저장할 것인가. generic·BOP·COCO·YOLO …
  config의 `writer.format`으로 고릅니다. 코드는 `sdg/writers/*.py`.

**B는 A의 부분집합을 재포장한 것.** 예: 6D pose 채널을 켜서 → BOP 포맷으로 저장. 2D bbox 채널을 켜서 →
YOLO 포맷으로 저장. **같은 렌더링, 다른 포맷**이 가능합니다(무슨 태스크를 학습하느냐에 따라 writer만 교체).

---

## 3. (A) 데이터 종류 — annotator 채널과 "실제로 어느 파일을 보면 되나"

아래는 `config/example.yaml`(generic 포맷)로 생성한 `datasets/example/`을 기준으로 한 매핑입니다.
generic writer는 켠 채널을 **폴더별 raw 파일**로 떨궈서 학습용이자 검사용으로 가장 투명합니다.

| 채널 | 무엇인가 | 학습 태스크 | **실제 결과 위치 (generic)** | 코드 |
|---|---|---|---|---|
| **RGB** | 컬러 이미지 | 모든 것의 입력 | `datasets/example/rgb/000000.png` | collector.py:139 |
| **Depth** | 픽셀별 거리 (16-bit PNG, 값=mm) | depth estimation, 3D 복원 | `datasets/example/depth/000000.png` | collector.py:143 |
| **Semantic seg** | 픽셀 = "이 **클래스**"(obj/ground…) | 의미 분할 | `datasets/example/semantic/000000.png` + `meta/*.json`의 `semantic_id_to_labels`(픽셀값→라벨) | collector.py:150 |
| **Instance seg** | 픽셀 = "이 **개별 객체 #n**" | 인스턴스 분할 | `datasets/example/instance/000000.png` + `meta/*.json`의 `instance_id_to_labels` | collector.py:157 |
| **2D bbox** | 화면상 사각 박스 | object detection | `meta/000000.json` → `objects[].bbox_2d` | collector.py |
| **3D bbox** | 3D 공간 박스(extents·8코너·투영) | 3D detection | `meta/*.json` → `objects[].bbox_3d` *(config에서 켤 때만)* | collector.py:82 |
| **Keypoints** | 객체의 특정 3D 점 → 2D 투영(+가시성) | keypoint / pose | `meta/*.json` → `objects[].keypoints` *(`assets/obj/<id>/keypoints.json` 필요, 켤 때만)* | collector.py |
| **6D pose** | 객체의 회전 R + 위치 t (카메라 기준) | 6D pose estimation | `meta/*.json` → `objects[].pose_cam` (4×4, column-vector·metres) | collector.py:253 |
| **Amodal mask / visib_fract** | 가려진 부분까지 포함한 전체 실루엣 + 가시비율 | occlusion-aware 학습 | BOP 포맷에서: `mask/`(amodal) vs `mask_visib/`(visible) + `scene_gt_info.json`의 `visib_fract` | collector.py `capture_amodal` |
| **Meta** | 카메라 intrinsics(K) + extrinsics(포즈) | 모든 GT의 기하 기준 | `meta/000000.json` → `intrinsics`, `camera_pose_world` | collector.py:119 |

### "실제로 열어보는 법" 예시

```bash
# RGB / depth / seg 는 그냥 이미지 뷰어. depth 는 16-bit라 값이 mm.
ls datasets/example/rgb/

# 한 프레임의 모든 기하 GT (카메라 K, 카메라 포즈, 객체별 6D pose·2D bbox)를 한눈에:
/isaac-sim/python.sh -c "import json;print(json.dumps(json.load(open('datasets/example/meta/000000.json')),indent=2)[:1500])"

# 라벨을 이미지 위에 그려서 눈으로 검증 (bbox/pose축/키포인트 오버레이):
ls datasets/example/qa/          # tools/visualize.py 가 만든 오버레이
```

`meta/000000.json` 실제 키 구조(예시로 생성한 것 그대로):

```
frame_id, sensor, intrinsics{fx,fy,cx,cy,...}, camera_pose_world(4x4),
objects[ {obj_id, instance_id, pose_cam(4x4), bbox_2d}, ... ],
semantic_id_to_labels{픽셀값: 라벨}, instance_id_to_labels{...}
```

> 참고: `objects[]` 안에 어떤 필드가 들어오는지는 **켠 채널에 달림**. example.yaml은 pose_6d·bbox_2d만 켜서
> `pose_cam`·`bbox_2d`만 보입니다. bbox_3d·keypoints를 config에서 켜면 그 필드도 같이 채워집니다.

---

## 4. (B) 포맷 — writer와 결과 경로

같은 채널을 다운스트림 학습 프레임워크가 바로 먹는 표준 구조로 직렬화합니다. 현재 4종 구현·검증 완료.

| 포맷 (`writer.format`) | 담는 것 | 소비하는 쪽 | **결과 경로 예시** |
|---|---|---|---|
| **generic** | 모든 채널 raw (위 3장) + QA 오버레이 | 자체 검사·커스텀 파이프라인 | `datasets/example/{rgb,depth,semantic,instance,meta,qa}/` |
| **BOP** | 6D pose 표준: `scene_gt.json`(R/t), `scene_camera.json`(K), `mask`/`mask_visib`, `scene_gt_info.json`(visib_fract) | SAM6D · FoundationPose 등 6D pose | `datasets/bop10/train_pbr/000000/` |
| **COCO** | detection/seg: `annotations/instances_*.json`(bbox·area·polygon) | Detectron2 · MMDetection | `datasets/coco20/{images,annotations}/` |
| **YOLO** | 정규화 라벨 `cls xc yc w h` + `data.yaml` | Ultralytics YOLO | `datasets/yolo20/{images,labels}/` |

BOP 한 scene 내부(가장 헷갈리는 포맷이라 펼침):

```
datasets/bop10/
├── camera.json              # 데이터셋 공통 카메라 스펙
├── obj_id_map.json          # obj_id 문자열 → 정수 매핑
└── train_pbr/000000/
    ├── rgb/ depth/          # 이미지·깊이
    ├── mask/                # amodal (가려진 부분 포함 전체 실루엣)
    ├── mask_visib/          # visible only (실제 보이는 부분)
    ├── scene_camera.json    # 프레임별 cam_K (intrinsics)
    ├── scene_gt.json        # 프레임별·객체별 cam_R_m2c / cam_t_m2c (6D pose)
    └── scene_gt_info.json   # visib_fract (가시비율), bbox 등
```

> **주의(메모리에도 기록됨)**: BOP writer는 `models/*.ply`(CAD 메시)를 만들지 않습니다. ADD-S 평가·렌더 정합에
> CAD PLY가 필요하면 별도 변환 도구가 필요합니다. → `next-mass-generation` 참고.

---

## 5. Domain Randomization (왜 대량·다양해야 하나)

객체 하나를 고정해 두고 렌더만 반복하면 똑같은 그림 수천 장 = 쓸모없음. **프레임마다 무작위화**해서
"현실의 온갖 변형"을 커버해야 모델이 실제 환경에 일반화됩니다. 우리 randomizer 축(config `randomizers[]`):

- **lighting** — dome ambient + 색온도(warm↔cool) + overhead fixture(방향광·실제 그림자)
- **materials** — 객체/바닥의 roughness·metallic·색·텍스처
- **background** — HDRI 하늘, 사실적 바닥 텍스처, 환경 USD(warehouse/office/room…) 전환
- **camera** — 카메라 위치·각도
- **pose** — 객체 위치·회전 (rotation: none/yaw/uniform_euler/uniform_so3; origin:bottom 이면 바닥 안착)
- **distractors** — 방해 물체(clutter) 스폰·산포 (씬 아무 데나 → 가릴 수도 안 가릴 수도)
- **occluder** — 카메라-타깃 사이에 물체를 놓아 **부분 가림을 보장**(distractor 와 다름). 가림률은
  `visib_fract` GT 로 기록. occlusion-robust segmentation/pose 학습용

이게 있어서 **같은 obj_000이라도 조명·배경·각도·가림이 다 다른 수천 장**이 나옵니다.

---

## 6. surface normal / optical flow / cross-correspondence — 활용성이 낮은가?

**아니요. 낮지 않습니다. 다만 "우리가 지금 표적으로 삼은 태스크(2D/3D detection, 6D pose)에 덜 쓰일 뿐"입니다.**
Replicator 엔진은 이 채널들을 원리적으로 다 뽑을 수 있고, 각자 확실한 용도가 있습니다:

| 채널 | 무엇인가 | 대표 활용 | 우리 프레임워크 현황 |
|---|---|---|---|
| **surface normal** | 픽셀별 표면 법선 벡터(면이 향하는 방향) | 표면 재구성, normal estimation, 조명/역렌더링, 6D pose 보조 | **✅ 완료** — `annotators.normals: true` → `datasets/<run>/normal/000000.png`(8-bit RGB normal map, 인코딩 `n*0.5+0.5`, 디코드 `n=png/255*2−1`, 배경=grey128). 검증: 지오메트리 위 `\|normal\|=1.000` |
| **optical flow (motion vector)** | 연속 프레임 간 픽셀 이동 벡터 | 비디오/시계열 학습, 모션 추정, 동적 씬 tracking | 미배선. **정지 씬 single-frame SDG엔 무의미**(연속 프레임/움직임이 있어야 값이 생김) → 우리가 안 붙인 진짜 이유는 "활용성"이 아니라 "현재 워크플로가 프레임 독립이라서" |
| **cross-correspondence** | 두 뷰/프레임 간 같은 3D점의 픽셀 대응 | 스테레오·멀티뷰 매칭, dense correspondence 학습(예: 광학흐름/뎁스의 GT) | 미배선. 멀티뷰 셋업이 전제 |

### optical flow — 구체적 활용 예시

"프레임 t의 각 픽셀이 프레임 t+1에서 **어디로 이동했는가**"를 (dx, dy) 벡터로 담은 채널. 움직임이 있어야 값이 생깁니다.

- **로봇 그리퍼 tracking**: 컨베이어 위를 흘러가는 부품을 로봇이 집을 때, 부품 픽셀들의 흐름 벡터로 **이동 속도·방향**을 학습 → 움직이는 대상 실시간 추종.
- **비디오 object segmentation/tracking**: 첫 프레임에만 마스크를 주고, 이후 프레임은 flow로 마스크를 **전파(propagate)**. flow GT가 있으면 tracker를 지도학습으로 훈련 가능.
- **모션 블러/롤링셔터 보정 학습**: 빠르게 움직이는 물체의 flow를 알면 블러를 역산하는 네트워크의 정답으로 사용.
- **Sim2real 동적 씬**: 실제 컨베이어·팔레타이징 라인처럼 물체가 계속 움직이는 환경의 인식 모델은, 정지 이미지가 아니라 flow가 포함된 **시퀀스**로 학습해야 일반화됨.

> 우리 프레임워크가 아직 안 붙인 이유: 현재 루프가 **프레임마다 pose를 새로 랜덤화**(프레임 간 연속성 없음)라 flow가 정의되지 않음. 붙이려면 "객체를 연속 궤적으로 움직이는 sequence 모드"가 선행돼야 함.

### cross-correspondence — 구체적 활용 예시

"뷰 A의 픽셀 ↔ 뷰 B의 같은 3D 점 픽셀"의 **대응 관계**. 두 대 이상의 카메라(또는 두 시점)가 전제입니다.

- **스테레오 depth 학습**: 좌·우 카메라 이미지에서 같은 점의 픽셀 대응(=disparity)이 GT → 스테레오 매칭 네트워크(예: RAFT-Stereo) 훈련. **실사에선 이 GT를 얻기가 매우 어려워** SDG의 대표 강점.
- **멀티뷰 6D pose / feature matching**: 여러 각도에서 찍은 같은 부품의 대응점으로 **local feature descriptor**(예: 매칭용 keypoint)를 학습 → 한 뷰에서 못 본 면을 다른 뷰로 보완.
- **NeRF/3D 재구성·novel view synthesis**: 뷰 간 정확한 대응이 있으면 형상·재질 복원 네트워크의 감독 신호가 됨.
- **hand-eye / 카메라 캘리브레이션 검증**: 알려진 대응으로 카메라 간 상대 포즈 추정 정확도를 평가.

> 우리 프레임워크가 아직 안 붙인 이유: 현재 센서가 **단일 카메라**. 붙이려면 멀티-카메라 rig(같은 씬을 여러 render product로) + 대응 export가 선행.

**요약**: "활용성 낮음"이 아니라 **"태스크 의존적"**. 우리가 지금 만드는 데이터셋(단일 프레임·단일 카메라 detection·pose)에
optical flow는 "움직임", cross-correspondence는 "멀티뷰"라는 **전제 셋업이 없어서** 쓸 자리가 없을 뿐입니다. 비디오
tracking(→ flow)이나 스테레오/멀티뷰(→ correspondence)로 확장하면 그때 붙이면 됩니다 — **원칙3(코드 아님,
config/plugin 확장)** 그대로, sequence/multi-camera 모드 + `collector.py` 채널 추가로 대응하도록 설계돼 있습니다.
surface normal은 이런 전제가 필요 없어서 **바로 붙여 완료**했습니다(위 표).

---

## 7. 다음에 읽을 것

- 실제로 20장씩 뽑아 검증하는 복붙 튜토리얼 → [`quick_start.md`](quick_start.md)
- 확장 축 5개·config 스키마·로드맵 → [`SDG.md`](SDG.md)
- Frame dict 계약(모든 writer가 보는 유일한 자료구조) → `sdg/writers/base.py` 상단 docstring
