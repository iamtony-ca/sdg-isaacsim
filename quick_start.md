# quick_start.md — 대량 생성 전 "20장 검증" 튜토리얼

대량(수천 장) 생성 전에 **각 데이터셋 포맷을 소량(≈20장)으로 먼저 돌려 문제없는지 확인**하는 복붙 가이드다.
아래 블록을 위에서 아래로 순서대로 실행하면 된다. 모든 명령은 워크스페이스 루트에서 실행:

```bash
cd /isaac-sim/volume/sdg_ws
```

> **렌더·에셋 변환은 반드시 번들 `/isaac-sim/python.sh`.** Isaac 이 필요 없는 단계(`--dry-run`,
> `tools/visualize.py`)는 시스템 `python3`(3.12, numpy·cv2·PIL 포함)로도 되고 기동이 훨씬 빠르다.
> 프레임 수는 config 값과 무관하게 **`--frames 20`** 으로 덮어쓸 수 있으므로, 기존 config 를 그대로 재사용한다.

---

## 0. 준비 (한 번만)

### 0-1. 새 객체 등록 — CAD 파일 하나에서 시작 (★ 새 객체를 받았다면 여기부터)

전제: 방금 `assets/cad/<폴더>/part.stp` 를 넣었다. 목표는 **`obj_id` 하나 만들기** —
그 뒤 모든 단계(1~8번)는 객체가 무엇이든 동일하다. (기존 `obj_000` 로 검증만 할 거면 0-2 로 건너뛴다.)

**① 변환 — CAD → `assets/obj/<obj_id>/mesh.usd`**

```bash
/isaac-sim/python.sh tools/import_cad.py "assets/cad/<폴더>/part.stp" --obj-id obj_001
```

- **백엔드는 확장자로 자동 선택**된다. 신경 쓸 필요 없지만 알아둘 것:
  | 입력 | 백엔드 | 비고 |
  |---|---|---|
  | `.stp` `.step` `.igs` `.iges` `.CATPart` `.sldprt` `.ipt` `.x_t` `.jt` `.prt` … (**B-rep CAD**) | `omni.kit.converter.hoops_core` (HOOPS Exchange) | 곡면을 삼각형으로 **테셀레이션**함 |
  | `.stl` `.obj` `.fbx` `.gltf` `.glb` (**이미 삼각형 메시**) | `omni.kit.asset_converter` | |
  전체 지원 목록은 `omni/kit/converter/hoops_core/impl/filters.py`.
  ⚠️ `.stp` 를 mesh 백엔드로 보내면 `UNSUPPORTED_IMPORT_FORMAT` 이다 — 라우팅은 자동이므로 그냥 두면 된다.
- B-rep 변환은 **수 분** 걸린다(SimulationApp 기동 + 테셀레이션). STL 은 1분 내외.
- 산출물 `assets/obj/obj_001/mesh.usd` 는 self-contained · **metres** · bbox 중심정렬이라 씬 빌더가 바로 참조한다.

**② 크기 검증 (★ 가장 중요 — 여기서 안 잡으면 데이터셋 전체가 틀린 스케일로 나온다)**

출력의 마지막 줄들을 반드시 읽는다:

```
[import_cad] --input-units auto -> metersPerUnit=0.0254 from the converted stage
[import_cad] final size (m): [0.181, 0.1762, 0.1529]  (expected ~[0.181, 0.1762, 0.1529])
```

- `final size (m)` 를 **실물 치수(도면)와 대조**한다. 맞으면 통과.
- 기본 `--input-units auto` 는 변환된 stage 의 `metersPerUnit` 을 읽는다. **B-rep 은 저작 단위를 파일에 갖고
  있어 auto 가 정확하다** (예: 이 STEP 은 inch 저작 → `0.0254`). 반면 **STL/OBJ 는 단위를 안 갖는다** →
  auto 가 경고를 찍고 mm 로 가정한다.
- **틀렸을 때만** 명시한다: `--input-units mm|cm|m|in`.
  증상은 배수로 나타난다 — 25.4배(inch↔mm), 1000배(m↔mm), 10배(cm↔mm).

**③ 곡면 품질 (B-rep 만)**

`--tess-lod 2`(기본). 렌더에서 원통·필렛이 각져 보이면 `3`~`4` 로 올린다(삼각형↑ = 파일·렌더 비용↑).

**④ 좌표계(up 축) 확인 — Y-up CAD 면 `--up-axis Y` 로 다시 임포트**

출력 `mesh.usd` 는 **항상 Z-up** 으로 만들어진다(USD 는 참조된 레이어의 `upAxis` 를 합성하지 않으므로
에셋에 Y 를 찍어봐야 무시된다). `--up-axis` 는 **소스가 무슨 축인지**를 알려주는 값이고, `Y` 를 주면
임포터가 **+90°(X축) 회전을 구워 넣어** Z-up 으로 세워준다.

```bash
/isaac-sim/python.sh tools/import_cad.py "assets/cad/<폴더>/part.stp" --obj-id obj_001 --up-axis Y
# 로그의 치수가 Y·Z 교환돼 나오면 회전이 들어간 것:  [0.181, 0.1762, 0.1529] -> [0.181, 0.1529, 0.1762]
```

`objects[].origin: bottom` 과 `rotation: yaw`(바닥 안착 + heading 다양성)는 로컬 AABB 의 **Z-최소**를
"바닥"으로 보므로, 여기서 축을 잘못 주면 ⑥ 렌더에서 물체가 **옆으로 눕고** 옆면이 바닥으로 잡힌다.
(로그에 변환기가 찍은 `converted stage reports upAxis=…` 가 항상 출력된다 — 참고값이지 진실은 아니다.)

**⑤ config 만들기 — 코드 수정은 없다(원칙3)**

```bash
sed -e 's/name: example/name: obj_001/' \
    -e 's|datasets/example|datasets/obj_001|' \
    -e 's/obj_id: obj_000/obj_id: obj_001/' \
    -e 's/class: obj_000/class: obj_001/' \
    config/example.yaml > config/obj_001.yaml
```

**⑥ 첫 렌더 3장 + 눈으로 확인**

```bash
/isaac-sim/python.sh sdg/run_sdg.py --config config/obj_001.yaml --frames 3 --headless
python3 tools/visualize.py datasets/obj_001          # QA 오버레이 (Isaac 불필요)
```

> ✅ 통과 기준: `datasets/obj_001/rgb/` 에 **물체가 실제로 보이고**, `qa/` 오버레이에서 bbox 가 물체를
> 감싸고 pose 축이 바닥 중앙에서 뻗는다. `meta/000000.json` 의 `objects` 가 비어있지 않아야 한다.
> **물체가 안 보이는데 에러도 없으면** 지오메트리가 비었다는 뜻이다(에셋 함정) — ②의 크기 출력과
> `--tess-lod` 를 먼저 의심.

**⑦ 재현성 등록 (권장)**

`mesh.usd` 는 gitignore 다. fresh clone 에서 자동 재생성되도록 `tools/setup_assets.py` 의
`OBJECT_IMPORTS` 에 한 줄 추가한다(CAD 소스는 git 추적):

```python
{"obj_id": "obj_001", "cad": "assets/cad/<폴더>/part.stp", "units": "auto", "up_axis": "Z"},
# up_axis 는 ④에서 확정한 "소스의 up 축" 을 그대로 적는다(Y-up CAD 면 "Y").
```

**⑧ (선택) GT 채널 추가**

- `assets/obj/obj_001/keypoints.json` — object-local 3D 점 → 매 프레임 2D 투영 + 가시성.
- `assets/obj/obj_001/parts.json` — 서브프림에 semantic class → 부분 mask(예: 표준 플랜지만).

이제 아래 0-2 부터 순서대로, `config/example.yaml` 대신 `config/obj_001.yaml` 을 쓰면 된다.

---

### 0-2. 에셋 부트스트랩 (gitignore 된 것 재생성)

`mesh.usd`, 바닥 텍스처, HDRI 하늘은 git 에 없다(용량). clone 직후 한 번 재생성:

```bash
/isaac-sim/python.sh tools/setup_assets.py            # floors + skies + object mesh
```

- 이미 있으면 건너뜀(idempotent). 환경 USD 배경(9번 단계)까지 쓰려면: `--all` 또는 `--envs simple_room,office`.
- 무엇이 되는지 미리 보기: `--dry-run`.

### 0-3. 설치 없이 config 만 검사 (Isaac 미기동, 5초)

렌더 전에 config/registry 스캐폴딩만 빠르게 점검한다. **크래시·오타를 여기서 먼저 거른다:**

```bash
for c in smoke example bop10 coco20 yolo20 dr_demo; do
  echo "=== $c ==="; python3 sdg/run_sdg.py --config config/$c.yaml --dry-run || break
done
```

> 새 객체용 config 를 만들었다면 목록에 추가한다(예: `... dr_demo obj_001`).

정상이면 각 config 의 파싱 결과(축·plugin 이름)가 출력되고 에러 없이 끝난다.

---

## 1. Smoke — 객체 없이 파이프라인 sanity (3장)

가장 먼저. **오브젝트 에셋 없이** app→scene→sensor→annotator→writer 전 경로가 도는지만 본다.

```bash
/isaac-sim/python.sh sdg/run_sdg.py --config config/smoke.yaml --headless
```

**나오는 것** — `datasets/smoke/`:

```
rgb/000000.png        바닥+dome light 만 찍힌 640x480 RGB
depth/000000.png      16-bit PNG, 값=밀리미터
semantic/000000.png   클래스 id 맵
meta/000000.json      intrinsics + camera_pose_world
dataset.json          프레임 수·센서·클래스 요약
```

> ✅ 통과 기준: 3장이 생기고 `error.log` 가 **없다**. depth PNG 를 열어 배경이 0(검정), 바닥이 유효한 mm 값이면 정상.

---

## 2. Generic — 표준 멀티모달 (RGB/Depth/Seg/6D pose) 20장

가장 범용적인 출력. 객체(obj_000)를 넣고 RGB·depth·semantic·instance·2D bbox·6D pose 를 한 번에 뽑는다.

```bash
/isaac-sim/python.sh sdg/run_sdg.py --config config/example.yaml --frames 20 --headless
```

**나오는 것** — `datasets/example/`:

```
rgb/000000.png … 000019.png     1280x720 RGB
depth/000000.png                16-bit mm depth
semantic/  instance/            클래스/인스턴스 id 맵
meta/000000.json                프레임별 GT (아래 예시)
dataset.json
```

`meta/000000.json` 실제 예시 (핵심 필드):

```jsonc
{
  "intrinsics": { "fx": 952.2, "fy": 952.2, "cx": 640.0, "cy": 360.0, "width": 1280, "height": 720 },
  "camera_pose_world": [[...4x4...]],          // 카메라의 월드 포즈 (column-vector, metres)
  "objects": [{
    "obj_id": "obj_000", "instance_id": 0,
    "pose_cam": [[...4x4...]],                 // 카메라 좌표계 기준 물체 6D pose. det(R)=1 보장
    "bbox_2d": [540, 302, 197, 161],           // [x, y, w, h] 픽셀
    "bbox_3d": { "extents_min": [...], "extents_max": [...], "corners_2d": [...] }
  }]
}
```

> ✅ 통과 기준: 20장 + 각 meta 의 `objects` 가 비어있지 않고 `pose_cam` 회전이 정규직교(det≈1),
> `bbox_2d` 가 이미지(1280x720) 안. 아래 **10번 QA** 로 눈으로 확인하는 걸 강력 권장.

---

## 3. BOP — 6D pose 표준 포맷 20장

SAM6D/FoundationPose 등 6D pose 소비자가 먹는 BOP 구조. amodal mask(가림 무시 전체 마스크)까지 포함.

```bash
/isaac-sim/python.sh sdg/run_sdg.py --config config/bop10.yaml --frames 20 --headless
```

**나오는 것** — `datasets/bop10/`:

```
train_pbr/000000/
  rgb/ depth/ mask/ mask_visib/    mask=amodal, mask_visib=가시부
  scene_camera.json                프레임별 K, depth_scale, cam_R/t_w2c
  scene_gt.json                    프레임별 obj별 cam_R_m2c, cam_t_m2c (OpenCV, mm)
  scene_gt_info.json               bbox_visib, visib_fract, px_count ...
camera.json  obj_id_map.json  bop_info.json
```

> ✅ 통과 기준: `scene_gt.json` 의 `cam_t_m2c` z 값이 양수(mm), `cam_R_m2c` det≈1.
> BOP 좌표는 내부(USD)→OpenCV 변환(+Y down,+Z fwd, ×1000) 이 적용된 상태.
> ⚠️ `models/*.ply`(CAD 3D 모델)는 이 writer 가 만들지 않는다 — 소비자에 별도 제공 필요.

---

## 4. COCO — 검출/세그멘테이션 20장

```bash
/isaac-sim/python.sh sdg/run_sdg.py --config config/coco20.yaml --frames 20 --headless
```

**나오는 것** — `datasets/coco20/`:

```
images/000000.png …
annotations/instances_train.json   COCO 표준: images / categories / annotations
```

`instances_train.json` 각 annotation: `{image_id, category_id, bbox:[x,y,w,h], area, iscrowd, segmentation:[폴리곤]}`
(category id 는 1-based, 폴리곤은 instance mask → cv2 contour).

> ✅ 통과 기준: `images` 수 = 20, 모든 `bbox` 가 이미지 안, `segmentation` 폴리곤이 존재.
> `split` 을 바꾸려면 config `writer.split: val` → `instances_val.json`.

---

## 5. YOLO — Ultralytics 검출 20장

```bash
/isaac-sim/python.sh sdg/run_sdg.py --config config/yolo20.yaml --frames 20 --headless
```

**나오는 것** — `datasets/yolo20/`:

```
images/train/000000.png …
labels/train/000000.txt        각 줄: "cls xc yc w h"  (class 0-based, 좌표 [0,1] 정규화)
data.yaml                       names / nc / train / val 경로
```

> ✅ 통과 기준: 모든 `.txt` 좌표가 0~1 범위, `data.yaml` 의 `nc`/`names` 가 객체 수와 일치.
> 폴리곤 세그 라벨이 필요하면 config `writer.segmentation: true` (instance_seg 필요).

---

## 6. DR 쇼케이스 — 도메인 랜덤화 다양성 확인 (9장)

포맷보다 **랜덤화 품질**(조명 톤/그림자/바닥 텍스처/색상 변화)을 눈으로 검증하는 용도. bbox_3d 포함.

```bash
/isaac-sim/python.sh sdg/run_sdg.py --config config/dr_demo.yaml --headless
```

> ✅ 통과 기준: `datasets/dr_demo/rgb/` 를 훑어 프레임마다 조명(따뜻/차가움/저각도 그림자)·바닥(원목/타일/석재)·
> 객체 색이 **서로 다르게** 보이면 DR 정상. 체커보드 바닥이 보이면 텍스처 미로딩(→ 0-2 재실행).

---

## 7. (선택) 환경 USD 배경 — 방/사무실 안에 배치 (6장)

바닥 텍스처 대신 실제 3D 환경(office/simple_room) 안에 객체를 놓는다. **환경 에셋 로컬화 필요:**

```bash
/isaac-sim/python.sh tools/setup_assets.py --envs simple_room,office --steps envs   # 대용량, 한 번만
/isaac-sim/python.sh sdg/run_sdg.py --config config/env_offline.yaml --headless
```

> 네트워크가 되고 로컬 다운로드가 싫으면 대신 **온라인** 모드: `--config config/env_online.yaml`
> (클라우드 프리셋 직참조, 로컬 파일 불필요). **두 모드를 섞지 말 것.**
> ✅ 통과 기준: `datasets/env_offline/rgb/` 배경이 프레임마다 방↔사무실로 바뀌고, 객체가 바닥에 그림자를 드리움.

---

## 8. QA 오버레이로 눈으로 검증 (Isaac 불필요)

generic 포맷(2·6·7번 결과) 위에 GT(bbox_2d/bbox_3d wireframe/keypoints/pose 축)를 그려 눈으로 확인:

```bash
/isaac-sim/python.sh tools/visualize.py datasets/example --max 20
/isaac-sim/python.sh tools/visualize.py datasets/dr_demo
```

→ `datasets/<run>/qa/000000.png …` 생성. **박스가 물체를 정확히 감싸고 pose 축이 물체 원점에서 뻗으면 GT 정상.**
BOP/COCO/YOLO 는 이 툴 대상이 아니다(generic meta 를 읽음) — 그 포맷들은 위 각 "통과 기준"의 JSON 값으로 검증.

---

## 한 방에 전부 (복붙용)

앞의 dry-run 이 통과했다는 전제 하에, 4개 포맷 20장씩을 순차 생성:

```bash
cd /isaac-sim/volume/sdg_ws
/isaac-sim/python.sh tools/setup_assets.py
for run in "example generic" "bop10 bop" "coco20 coco" "yolo20 yolo"; do
  set -- $run
  echo "########## generating $1 ($2) ##########"
  /isaac-sim/python.sh sdg/run_sdg.py --config config/$1.yaml --frames 20 --headless || {
    echo "FAILED: $1 — datasets/$1/error.log 확인"; break; }
done
/isaac-sim/python.sh tools/visualize.py datasets/example --max 20
```

각 `datasets/<run>/` 가 생기고 `error.log` 가 없으면 **대량 생성 준비 완료**.
대량 생성은 config 의 `run.num_frames` 를 올리거나 `--frames <N>` 로 지정 (예: `--frames 5000`).

---

## 문제 해결 (자주 겪는 것)

| 증상 | 원인 / 조치 |
|---|---|
| `error.log` 가 생김 | fast-shutdown 이 traceback 을 삼켜 여기에 먼저 기록됨 — **먼저 이 파일을 볼 것**. |
| `UNSUPPORTED_IMPORT_FORMAT` | B-rep CAD(.stp 등)를 mesh 백엔드로 보냄. 현재 `import_cad.py` 는 자동 라우팅하므로, 이 에러가 보이면 확장자가 지원 목록(`hoops_core/impl/filters.py`)에 없는 것. |
| 물체가 25.4× / 1000× / 10× 크거나 작음 | 단위 오해석 — `import_cad.py --input-units in\|mm\|m\|cm` 로 명시 후 재변환. auto 는 B-rep 에만 신뢰. |
| 곡면(원통·필렛)이 각져 보임 | B-rep 테셀레이션이 성김 — `import_cad.py --tess-lod 3` (또는 4). |
| `import_cad.py` 가 성공한 듯한데 `mesh.usd` 없음 | 구버전은 fast-shutdown 탓에 실패해도 exit 0 이었다. 현재는 **exit 2 + stderr** 로 알린다 — 메시지를 볼 것. |
| 객체가 안 보임(빈 바닥) | mesh 미로딩 → `tools/setup_assets.py --force`. 또는 카메라 near clip / pose z 확인. |
| 바닥이 체커보드 | 바닥 텍스처 미로딩 → `setup_assets.py --steps floors --force`. |
| frame 0 에서 크래시 | warm-up 관련 — 재실행하면 대개 해소(camera_params 준비 타이밍 의존). |
| 환경 USD 배경 검정/깨짐 | `--envs` 로 해당 env 를 먼저 로컬화했는지 확인(단일 파일 복사로는 깨짐). |
| `ModuleNotFoundError: omni…` (시스템 python3) | Isaac API 는 번들 파이썬에만 있다 — 렌더/변환은 `/isaac-sim/python.sh`. |
