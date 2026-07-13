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

### 3-1. DR 배경/재질 에셋 풀 (★ 온라인/오프라인 분리)

랜덤화기가 매 프레임 바꾸는 **바닥 재질**·**dome HDRI 하늘**·**환경 USD 배경**은 Isaac Sim 이 로컬에
번들하지 않고 **NVIDIA 클라우드 assets 서버**에 둔다. 두 모드를 **명확히 분리**해서 쓴다:

- **온라인 모드**: config 가 클라우드 키워드/프리셋을 직접 참조 → 생성 시 네트워크 필요, 로컬 파일 불필요.
  (`hdri: isaac_skies[:Indoor,Night]`, `background` randomizer `pool: [warehouse, office, …]`.)
  예시 config: `config/env_online.yaml`.
- **오프라인 모드**: `tools/fetch_isaac_assets.py` 로 에셋을 repo 로 **1회 다운로드/로컬화** → config 는
  로컬 경로만 참조 → 생성 시 **네트워크 불필요**(에어갭 배포). 예시: `config/env_offline.yaml`,
  `config/dr_demo.yaml`. **섞지 말 것** — 한 config 는 온라인 또는 오프라인 중 하나로.

```bash
# 오프라인 로컬화 (온라인 1회 실행). 카테고리별 독립 — 원하는 것만:
/isaac-sim/python.sh tools/fetch_isaac_assets.py --all                    # 바닥+하늘 (envs 제외)
/isaac-sim/python.sh tools/fetch_isaac_assets.py --envs simple_room,office # 환경 USD (opt-in, 대용량)
/isaac-sim/python.sh tools/fetch_isaac_assets.py --floors --dry-run        # 대상 URL 만 출력
```

로컬화 대상 → 위치:
- **바닥 텍스처**(원목/석재/타일/자갈/대리석 ~50종) → `assets/textures/ground/`  (`--floors`)
- **HDRI 하늘**(Clear/Cloudy/Indoor/Night 15종) → `assets/env/hdri/`  (`--skies`)
- **환경 USD 배경**(warehouse/office/simple_room/hospital/grid…) → `assets/env/usd/<name>/`  (`--envs`,
  대용량이라 `--all` 에 미포함·opt-in). 환경은 **의존성까지 수집**(`omni.kit.usd.collect.Collector`:
  stage + 머티리얼 + 텍스처 + props) 해야 오프라인에서 안 깨진다. (예: office ≈680MB, simple_room ≈120MB.)

- 다운로드 바이너리는 gitignore(서드파티, `mesh.usd` 와 동일 정책 — 툴로 재생성). **오프라인 배포 시
  배포 번들에 `assets/textures/ground/`·`assets/env/hdri/`·`assets/env/usd/` 를 포함**해야 한다.
- **정확한 클라우드 URL 은 `assets/ASSET_SOURCES.md`(git 추적)에 자동 기록** — 처음부터 다시 셋업할 때
  참고용. **섹션별 병합**이라 `--envs` 만 재실행해도 바닥/하늘 기록이 지워지지 않는다. 소스 정의(어떤
  클라우드 dir 을 열거/큐레이트/collect 하는지)는 `sdg/assets.py` 상단 주석.
- API(전부 6.0.1 설치본 대조, 추측 없음): `omni.client.list`(dir 열거), `omni.client.copy(...OVERWRITE)`
  (파일 다운로드), `omni.kit.usd.collect.Collector`(환경 의존성 수집), 루트는
  `isaacsim.storage.native.get_assets_root_path()`.

## 4. 재현 절차 (fresh clone → 첫 렌더)

```bash
# 0) Isaac Sim 6.0.1 이 /isaac-sim 에 설치되어 있어야 함 (위 표)
git clone <this-repo> && cd sdg_ws

# ★ 한 방 부트스트랩: gitignore 된 에셋(바닥/하늘/환경USD + CAD->mesh.usd) 을 전부 재생성.
#   (fetch_isaac_assets + import_cad 를 순서대로 subprocess 로 호출. envs 는 대용량이라 opt-in.)
/isaac-sim/python.sh tools/setup_assets.py            # floors + skies + objects (기본)
/isaac-sim/python.sh tools/setup_assets.py --all      # + 환경 USD (office~680MB 등)
/isaac-sim/python.sh tools/setup_assets.py --dry-run  # 실행할 명령만 출력(다운로드 X)
#   idempotent: 이미 채워진 dir 은 건너뜀(--force 로 강제 재생성). 아래 1~4 를 자동화한 것.

# 1) 순수 파이썬 레이어 확인 (Isaac 미기동)
/isaac-sim/python.sh sdg/run_sdg.py --config config/smoke.yaml --dry-run

# 2) 에셋 없이 S1 파이프라인 렌더 검증 (ground+dome, rgb/depth/semantic)
/isaac-sim/python.sh sdg/run_sdg.py --config config/smoke.yaml --headless
#   -> datasets/smoke/{rgb,depth,semantic,meta}/000000.png ... + dataset.json

# 3) (선택) DR 배경/재질 풀 로컬화 — 사실적 바닥·하늘 (온라인 1회, §3-1)
/isaac-sim/python.sh tools/fetch_isaac_assets.py

# 4) 실제 오브젝트로 생성 (assets/obj/obj_000/ 에 USD 배치 후)
/isaac-sim/python.sh sdg/run_sdg.py --config config/example.yaml --headless
```

시드(`run.seed`)와 `config_snapshot.yaml`(출력 폴더에 자동 저장)로 재현성을 보장한다.

## 5. 권한 문제 (Isaac 을 root 로 실행했을 때) — `tools/fix_perms.sh`

이 컨테이너의 정상 유저는 **`isaac-sim`**(uid 1234)이고 **`sudo` 가 없다.** 누군가(예: 자동화 에이전트)
Isaac Sim 을 **root 로 실행**하면 공유 캐시에 root 소유 파일이 남아 isaac-sim 이 못 쓰게 되고, 이후
`~/runapp.sh` / `~/runheadless.sh` 가 제대로 안 뜬다. 오염되는 곳:
`/isaac-sim/kit/{cache,logs,data}`, `/isaac-sim/.nv`, `/isaac-sim/.cache`, `/isaac-sim/.nvidia-omniverse`,
`/isaac-sim/exts/omni.pip.{cloud,compute}`, 그리고 이 워크스페이스.

**isaac-sim 계정에서는 직접 못 고친다(sudo 없음).** 반드시 **root 컨텍스트**에서 복구:

```bash
# 감지만 (아무 계정이나 가능)
sh tools/fix_perms.sh --check

# 복구 — 컨테이너 안 root 쉘에서
sh tools/fix_perms.sh

# 복구 — 호스트에서 (컨테이너 이름이 예: tony)
docker exec -u root tony sh /isaac-sim/volume/sdg_ws/tools/fix_perms.sh
```

`tools/fix_perms.sh` 는 위 디렉토리 + 워크스페이스의 **root 소유 파일만** isaac-sim 소유로 되돌린다
(idempotent, `SDG_RUNTIME_USER`/`ISAAC_SIM_ROOT` 로 오버라이드 가능).

**예방(권장):** 애초에 root 로 Isaac 을 돌리지 말 것. root 쉘에서 실행해야 하면 isaac-sim 으로 강등:
```bash
runuser -u isaac-sim -- /isaac-sim/python.sh sdg/run_sdg.py --config config/example.yaml --headless
```
이러면 캐시가 isaac-sim 소유로 유지되어 오염이 안 생긴다.
