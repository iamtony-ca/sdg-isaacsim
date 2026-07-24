# sdg_ws — Synthetic Data Generation (Isaac Sim 6.0.1)

Isaac Sim 6.0.1 위 **범용·확장 가능한 synthetic data generation 환경**. 특정 태스크/객체 무종속,
config·plugin 구동. 설계는 [`SDG.md`](SDG.md), 세션 컨텍스트·원칙은 [`CLAUDE.md`](CLAUDE.md),
다운스트림 6D pose 소비 사례 검토·되먹임 요구사항은 [`CONSUMER_6DPOSE.md`](CONSUMER_6DPOSE.md).

## 현재 상태
- ✅ **S0 스캐폴딩**: 폴더 구조 + 문서 + 순수 파이썬 골격(config/registry/generic writer).
- ✅ **S1 (구현+검증)**: 6.0.1 Replicator/센서 API 조사 완료, `app`/`scene`/`sensors`/`annotators`/
  `randomizers`/`run_sdg` 실제 구현. `config/smoke.yaml`(오브젝트 없이 ground+dome)로 **headless 3프레임
  렌더 검증 완료** — RGB/16-bit depth(mm)/semantic + meta(intrinsics·metric camera pose)·dataset.json 생성.
- ✅ **S3**: **구현·검증 완료** — `materials`(오브젝트별 OmniPBR: roughness/metallic/색 매 프레임),
  `distractors`(풀에서 1~N개 clutter spawn·scatter·라벨링), `occluder`(카메라-타깃 시선 위 배치로 **부분
  가림 보장** — visib_fract GT; `config/occluder_test.yaml`), **keypoint annotator**(obj-local 3D → 2D 투영+가시성,
  `keypoints.json`), **bbox_3d writer 필드**(extents·transform·camera-frame corners·2D 투영·occlusion),
  그리고 **writer 3종**: `bop`(scene_camera/scene_gt/scene_gt_info + rgb/depth/mask, model→cam OpenCV mm),
  `coco`(instances_json: bbox·area·segmentation), `yolo`(정규화 label + data.yaml).
- ✅ **S4**: **구현·검증 완료** — `realsense_depth` 센서(bias+거리²노이즈+edge dropout+speckle hole),
  **amodal mask**(`annotators.amodal` — 오브젝트별 격리 렌더 → BOP `mask/`·실제 `visib_fract`),
  **QA 시각화** `tools/visualize.py`(rgb 위 bbox/keypoint/3D-wireframe/pose축 오버레이).
- ✅ **S2 MVP**: 실제 CAD(`assets/cad/6-inch-wafer-cassette/`)를 `tools/import_cad.py` 로 USD 변환 →
  `assets/obj/obj_000/mesh.usd` → `config/example.yaml` 로 렌더 검증(라벨·bbox·6D pose·QA 오버레이 확인).
  > mesh.usd 는 gitignore — clone 후 `import_cad.py` 로 재생성(CAD 소스는 git 추적). [`DEPENDENCIES.md`](DEPENDENCIES.md) 참조.

의존성·재현 절차는 [`DEPENDENCIES.md`](DEPENDENCIES.md) 참고 (추가 설치 0개 — 전부 Isaac 6.0.1 번들 내장).

## 실행
```bash
# 0) 순수 파이썬 레이어 확인 (SimulationApp 미기동)
/isaac-sim/python.sh sdg/run_sdg.py --config config/smoke.yaml --dry-run

# 1) 에셋 없이 S1 파이프라인 렌더 검증 (ground+dome, rgb/depth/semantic)
/isaac-sim/python.sh sdg/run_sdg.py --config config/smoke.yaml --headless
#   -> datasets/smoke/{rgb,depth,semantic,meta}/*.png|json + dataset.json

# 2) 실제 오브젝트로 생성 (assets/obj/obj_000/ 에 USD 배치 후)
/isaac-sim/python.sh sdg/run_sdg.py --config config/example.yaml --headless
```
> 이 컨테이너엔 시스템 `python3` 가 없다 — 항상 번들 `/isaac-sim/python.sh` 로 실행.

> **처음 배운다면**: SDG가 무슨 데이터를 만드는지·각 채널이 어느 파일에 나오는지 개념 정리는
> [`introduction.md`](introduction.md).
>
> **대량 생성 전 20장 검증**: 각 포맷(generic/bop/coco/yolo)을 소량으로 먼저 돌려보는 복붙 튜토리얼은
> [`quick_start.md`](quick_start.md). 프레임 수는 `--frames <N>` 로 오버라이드.

## 구조 (요약)
```
sdg/        프레임워크 (run_sdg / config / registry / app / scene / randomizers / sensors / annotators / writers)
config/     실행별 YAML
assets/obj/<obj_id>/   플러그인 객체 자산
datasets/   생성 출력 (gitignore)
calibration/ (옵션) real-sensor 참조
tools/      후처리·패키징·QA
```

## 원칙 (요약)
1. **범용이 목표** — 6D pose 등은 예시 소비 사례일 뿐, 프레임워크가 종속되지 않음.
2. **객체명 하드코딩 금지** — `obj` / `obj_id` 로 일반화.
3. **config·plugin 구동** — 새 태스크 = 새 config.
4. **6.0.1 API 추측 금지** — 설치본에서 확인 후 구현.
