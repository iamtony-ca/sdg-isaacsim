# DEPENDENCIES.md — reproducible environment for sdg_ws

이 파일은 **재현성** 을 위해 이 워크스페이스가 의존하는 모든 것을 명시한다. 다른 PC 에서
`git clone` 후 이 문서만 따라가면 동일 환경이 되도록 유지한다. **무언가를 설치하면 반드시 여기에
(이름·버전·이유) 기록한다.**

## 1. 실행 런타임 (필수)

| 항목 | 버전 | 비고 |
|---|---|---|
| **Isaac Sim** | **6.0.1** (`6.0.1-rc.7+release.42383`) | `/isaac-sim` 설치본. 실행은 번들 파이썬 `/isaac-sim/python.sh`. |
| Python (번들) | 3.12.13 | Isaac 번들에 포함 — 별도 설치 금지. |
| omni.replicator.core | 1.13.27 (+110.1.1) | Isaac 6.0.1 번들에 포함. SDG 코드가 이 API 에 맞춰 작성됨. |
| GPU | RTX 5090 (32GB), NVIDIA 드라이버 | headless RTX 렌더링에 필요. |

> **핵심:** 이 프로젝트는 Isaac Sim 번들 파이썬으로만 실행한다. colcon/ROS 아님, 별도 venv 아님.

## 2. 파이썬 패키지

**추가 설치한 것: 없음 (0개).** SDG 프레임워크가 쓰는 서드파티 패키지는 전부 Isaac 6.0.1 번들
파이썬에 **이미 포함**되어 있어, 별도 `pip install` 이 필요 없다.

| 패키지 | 번들 버전 | 용도 |
|---|---|---|
| numpy | 2.3.1 | 배열/포즈 계산 (collector, writer) |
| PyYAML | 6.0.3 | config 로드 (`sdg/config.py`) |
| Pillow (PIL) | 12.2.0 | PNG 인코딩 (writers) |
| OpenCV (cv2) | 4.13.0 | COCO/YOLO segmentation 폴리곤 추출 (`sdg/writers/_shapes.py`) — 없으면 seg 생략, bbox 만 |

검증 명령 (다른 PC 에서 동일하게 나와야 함):
```bash
/isaac-sim/python.sh -c "import numpy,yaml,PIL,cv2;print(numpy.__version__,yaml.__version__,PIL.__version__,cv2.__version__)"
# 기대: 2.3.1 6.0.3 12.2.0 4.13.0  (Isaac 6.0.1 번들 기준)
```

만약 향후 번들에 없는 패키지가 필요해지면:
```bash
/isaac-sim/python.sh -m pip install <pkg>==<ver>   # ← 반드시 이 표에 (이름/버전/이유) 추가할 것
```

## 3. 에셋 (CAD 소스는 git 추적, 변환된 USD 는 gitignore)

- **CAD 소스**(`assets/cad/<name>/`, 예: `6-inch-wafer-cassette/`의 stl/stp/ipt/jpg)는 **git 에 추적**
  → clone 후 재현 가능. `.stl` 이 파이프라인 진입점.
- **변환된 USD**(`assets/obj/<obj_id>/mesh.usd`)는 gitignore. clone 후 `tools/import_cad.py` 로 **재생성**:
  ```bash
  /isaac-sim/python.sh tools/import_cad.py \
    "assets/cad/6-inch-wafer-cassette/Wafer Cassette_6 Inch - 25 Wafer Capacity.stl" \
    --obj-id obj_000 --input-units mm --up-axis Z
  ```
  (STL/OBJ/FBX → USD 변환 `omni.kit.asset_converter`, 입력단위→metres 스케일 + bbox 중심정렬,
  self-contained `mesh.usd` 생성.) 각 obj 디렉토리에 `*.usd*` 하나 있으면 `obj_id` 로 자동 인식.
- `datasets/` 는 생성 출력물 — gitignore.
- 오브젝트 에셋 없이 파이프라인만 검증하려면 `config/smoke.yaml`(objects 비어 있음) 사용.

## 4. 재현 절차 (fresh clone → 첫 렌더)

```bash
# 0) Isaac Sim 6.0.1 이 /isaac-sim 에 설치되어 있어야 함 (위 표)
git clone <this-repo> && cd sdg_ws

# 1) 순수 파이썬 레이어 확인 (Isaac 미기동)
/isaac-sim/python.sh sdg/run_sdg.py --config config/smoke.yaml --dry-run

# 2) 에셋 없이 S1 파이프라인 렌더 검증 (ground+dome, rgb/depth/semantic)
/isaac-sim/python.sh sdg/run_sdg.py --config config/smoke.yaml --headless
#   -> datasets/smoke/{rgb,depth,semantic,meta}/000000.png ... + dataset.json

# 3) 실제 오브젝트로 생성 (assets/obj/obj_000/ 에 USD 배치 후)
/isaac-sim/python.sh sdg/run_sdg.py --config config/example.yaml --headless
```

시드(`run.seed`)와 `config_snapshot.yaml`(출력 폴더에 자동 저장)로 재현성을 보장한다.
