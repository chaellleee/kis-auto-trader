# KIS Auto Trader — 한국투자증권 API 기반 자동매매 시스템

한국투자증권(KIS) Open API를 사용한 국내주식(KOSPI + KOSDAQ) 자동매매 시스템입니다.
**다중 팩터 모멘텀(Multi-Factor Momentum)** 전략으로 시장(KOSPI) 대비 초과수익(알파)을 목표로 합니다.

> ⚠️ **먼저 읽어주세요.**
> 이 시스템은 `paper`(모의투자) 모드와 `real`(실전투자) 모드를 모두 지원합니다.
> **기본값은 모의투자**입니다. 실전 전환은 충분한 백테스트·모의투자 검증 후 본인 책임 하에 진행하세요.
> 어떤 전략도 미래 수익을 보장하지 않으며, 투자 손실의 책임은 전적으로 사용자에게 있습니다.

---

## 1. 핵심 설계

| 구성요소 | 내용 |
|---|---|
| **유니버스** | KOSPI + KOSDAQ 전체 → 유동성/시총 필터로 상위 종목 압축 |
| **전략** | 12-1 모멘텀 · 단기 모멘텀 · 저변동성 · 추세 4개 팩터 결합 |
| **시장방어** | 코스피가 200일선 아래(약세장)면 현금 비중 확대 (regime filter) |
| **리스크** | 종목당 비중 상한, 변동성 타게팅, 손절/익절 |
| **검증** | 백테스트로 벤치마크(KOSPI) 대비 CAGR·Sharpe·MDD 비교 |
| **실행** | 모의/실전 도메인·TR_ID를 한 줄 설정(`KIS_MODE`)으로 전환 |

### 왜 이 전략이 "시장 초과수익"을 노릴 수 있나 (한국형 멀티팩터)

기본 전략(`korea_multifactor`)은 **한국 시장에서 실증적으로 강건한 프리미엄**을 결합합니다. 한국은 단순 가격 모멘텀이 약한 대표적 시장이라(개인 비중·잦은 반전), 가격 모멘텀 대신 아래 팩터를 코어로 씁니다.

1. **가치 (Value)** — 저PER·저PBR·고배당. 한국에서 가장 견조하게 재현되는 프리미엄.
2. **저변동성 (Low-Volatility)** — 변동성 낮은 종목이 위험 대비 초과수익. 한국에서 특히 강함.
3. **사이즈 (Size)** — 소형주 우위. 한국 실증에서 프리미엄이 가장 큼(유동성 필터로 초소형주 위험은 차단).
4. **수급 (Supply/Demand)** — 외국인+기관 순매수 흐름. **한국 특화** 신호로 예측력이 큼.
5. **퀄리티 (Quality)** — ROE 프록시. 한국선 단독 효과가 약해 **보조 비중(0.10)** 으로만.

| 팩터 | 가중치 | 데이터 소스 |
|---|---|---|
| **value** | 0.30 | 펀더멘털(PER/PBR/DIV) 패널 |
| **low_volatility** | 0.25 | 가격(close)에서 직접 계산 |
| **size** | 0.20 | 시가총액 패널 |
| **supply_demand** | 0.15 | 투자자별 순매수 패널 |
| quality | 0.10 | ROE 프록시 패널 |

각 팩터는 횡단면 z-score 후 가중합합니다. **패널 데이터가 없으면 해당 팩터는 자동 비활성**되고 남은 팩터의 가중치를 재정규화하므로, 데이터가 전혀 없어도 저변동성 단일 팩터로 동작합니다. 월별 리밸런싱(가치·사이즈는 느린 팩터)에 버퍼존+턴오버 상한으로 비용을 억제합니다.

> 가격 모멘텀 전략(`multifactor_momentum`)도 레거시로 남아 있습니다. `config.yaml` 의 `strategy.name` 으로 전환할 수 있습니다.

### 회전율(턴오버) 억제

- **버퍼존(히스테리시스)** — 보유 종목이 `holding × buffer_zone` 순위 안이면 약한 가점으로 유지해 잦은 교체를 줄임. `strategy.buffer_zone`.
- **턴오버 상한** — 1회 리밸런싱에서 전체 자산의 일정 비율(`turnover_limit`)까지만 교체. 부분 이동(`old + α·(target−old)`). `execution.turnover_limit`.

---

## 2. 폴더 구조

```
kis-auto-trader/
├── README.md
├── requirements.txt
├── .env.example          # 복사 → .env 에 API 키 입력
├── config/config.yaml    # 전략·리스크 파라미터 (코드 수정 없이 튜닝)
├── src/
│   ├── kis_api/          # KIS Open API 래퍼
│   │   ├── auth.py           # OAuth 토큰 발급·캐싱
│   │   ├── client.py         # 실전/모의 자동 분기 클라이언트
│   │   ├── quotations.py     # 시세·일봉 조회
│   │   └── trading.py        # 주문(매수/매도)·잔고 조회
│   ├── data/
│   │   ├── universe.py       # KOSPI/KOSDAQ 종목 로딩 + 일봉 수집/캐싱
│   │   └── fundamentals.py   # 퀄리티(ROE/이익수익률) 펀더멘털 패널
│   ├── strategy/         # 전략 엔진
│   │   ├── base.py
│   │   └── multifactor_momentum.py
│   ├── portfolio/manager.py  # 포지션 사이징·리스크 관리
│   ├── backtest/engine.py    # 백테스트 + 성과지표
│   └── utils/logger.py
├── scripts/
│   ├── fetch_universe.py     # 유니버스/일봉 데이터 캐시 생성
│   ├── run_backtest.py       # 백테스트 → 시장 대비 성과 리포트
│   └── run_paper_trading.py  # 모의투자 자동매매 1회 실행
└── tests/test_strategy.py
```

---

## 3. 설치

```bash
git clone <this-repo>
cd kis-auto-trader
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 환경 분리: 최상위 .env 는 모드 선택만, 자격증명은 env/ 안에 모드별로 둡니다.
cp .env.example .env                          # KIS_MODE=paper|real 선택
cp env/.env.paper.example env/.env.paper      # 모의투자 앱키/시크릿/계좌
cp env/.env.real.example  env/.env.real       # 실전투자 앱키/시크릿/계좌 (선택)
```

#### 환경(Env) 구조

| 파일 | 역할 | 커밋 |
|---|---|---|
| `.env` | `KIS_MODE` 로 **모의/실전 선택만** | ✗ |
| `env/.env.paper` | 모의투자 전용 키·계좌 (도메인 :29443) | ✗ |
| `env/.env.real` | 실전투자 전용 키·계좌 (도메인 :9443) | ✗ |
| `*.example` | 템플릿 | ✓ |

클라이언트는 `KIS_MODE` 값에 따라 **해당 모드의 env 파일만** 로드합니다. 모의 키와 실전 키가 한 파일에 섞이지 않아 실수로 실전 주문이 나가는 사고를 구조적으로 막습니다. 특정 파일을 직접 지정하려면 `.env` 의 `KIS_ENV_FILE` 을 쓰세요.

### KIS API 키 발급

1. [KIS Developers](https://apiportal.koreainvestment.com/) 가입
2. 한국투자증권 계좌 + **모의투자 신청** (모의투자 메뉴에서 별도 신청 필요)
3. 앱키(App Key) / 앱시크릿(App Secret) 발급 → `.env` 에 입력
4. 모의투자와 실전투자는 **앱키가 다릅니다.** 각각 발급받아 입력하세요.

---

## 3-1. Cursor에서 시작하기

이 레포는 Cursor에 맞춰 설정되어 있습니다.

- **프로젝트 규칙(자동 인지)** — `.cursor/rules/project.mdc` 가 항상 적용되어, Cursor AI가 아키텍처·KIS API TR_ID·**안전 규칙(모의 기본, 키 커밋 금지, --dry-run 유지)** 을 알고 코드를 생성·수정합니다. (레거시 호환용 `.cursorrules` 도 포함)
- **에디터 설정 적용** — 워크스페이스 설정/권장 확장 템플릿이 `cursor-setup/` 에 있습니다. 한 번만 복사하세요.

  ```bash
  mkdir -p .vscode
  cp cursor-setup/settings.json   .vscode/settings.json
  cp cursor-setup/extensions.json .vscode/extensions.json
  ```

  이러면 ① `.venv` 인터프리터 자동 선택, ② 테스트 탭에서 pytest 실행, ③ 통합 터미널에 `PYTHONPATH` 자동 설정(=`python -m scripts.*` 바로 실행), ④ 권장 확장(Python/Pylance/Ruff 등) 설치 안내가 적용됩니다.

- **임포트 경로** — 루트 `conftest.py` 가 레포 루트를 `sys.path` 에 넣어, Cursor 테스트 러너든 터미널이든 `from src...` 가 그대로 동작합니다.
- **AI에게 일 시키는 팁** — Cursor 채팅에서 `@config/config.yaml`, `@src/strategy/multifactor_momentum.py` 처럼 파일을 멘션하면 규칙과 함께 정확한 맥락으로 수정해 줍니다.

## 4. 사용법 (권장 순서)

```bash
# (1) 유니버스 + 일봉 데이터 캐시 생성  (최초 1회, 이후 증분 업데이트)
python -m scripts.fetch_universe

# (2) 백테스트로 시장 대비 성과 검증  ← 실거래 전 필수
python -m scripts.run_backtest

# (3) 모의투자 자동매매 1회 실행 (KIS_MODE=paper 확인)
python -m scripts.run_paper_trading
python -m scripts.run_paper_trading --dry-run   # 주문 없이 계획만

# (4) 현재 잔고/보유종목 확인
python -m scripts.check_balance
```

`run_backtest` 는 `results/` 에 누적수익 곡선과 성과지표(CAGR, Sharpe, MDD, 승률, 벤치마크 대비 알파)를 저장합니다.
**먼저 백테스트에서 KOSPI를 유의미하게 이기는지 확인**한 뒤 모의투자로 넘어가세요.

### 장중 내내 자동 실행 (run_live)

`run_paper_trading` 은 1회 실행이지만, `run_live` 는 **장 시작~마감 동안 계속 떠 있으면서 자동으로** 돕니다.

```bash
python -m scripts.run_live              # 자동 실행 (KIS_MODE 에 따라 모의/실전)
python -m scripts.run_live --dry-run    # 주문 없이 동작만 로그로 확인 (권장 첫 테스트)
python -m scripts.run_live --once       # 한 사이클만 돌고 종료 (점검용)
```

동작은 `config.yaml` 의 `live:` 에서 조정합니다.

- **하루 1회 전체 리밸런싱** — `rebalance_at`(기본 09:05)에 신호 계산 후 매매.
- **장중 손절/익절 감시** — `risk_check_interval_min`(기본 5분)마다 보유종목 손익 점검 → -8% 손절, +25% 익절.
- 주말·장 시작 전·마감 후에는 자동으로 대기. `Ctrl+C` 로 안전 종료.

> 이 전략은 일봉 기반이라 '하루 1회 + 장중 리스크 감시' 가 합리적입니다. 분 단위 매매는 비용만 키워 의도적으로 제한합니다. 공휴일은 자동 감지하지 않지만, 데이터가 없으면 신규 매수를 스킵하므로 안전합니다.

#### 컴퓨터가 꺼지거나 절전되지 않게 (맥)

`run_live` 는 컴퓨터가 켜져 있고 터미널이 살아 있어야 돕니다.

```bash
# 절전 방지하며 실행 (맥 기본 제공 caffeinate)
caffeinate -i python -m scripts.run_live

# 터미널을 닫아도 백그라운드 유지 + 로그 파일로
nohup caffeinate -i python -m scripts.run_live > logs/live.out 2>&1 &
```

#### 매일 정해진 시각에만 자동 실행하고 싶다면 (cron)

데몬을 띄우는 대신 하루 1회만 돌리는 방법:

```bash
# crontab -e  — 매 영업일 09:05 에 1회 리밸런싱
5 9 * * 1-5  cd /path/to/kis-auto-trader && .venv/bin/python -m scripts.run_paper_trading
```

---

## 5. 실전 전환

`.env` 에서 한 줄만 바꾸면 됩니다. (키는 이미 `env/.env.real` 에 분리되어 있습니다.)

```
KIS_MODE=real
```

내부적으로 도메인·TR_ID가 실전용으로 전환되고, `env/.env.real` 의 자격증명만 로드됩니다.
한 번만 테스트하고 싶으면 환경변수로 일시 전환할 수도 있습니다: `KIS_MODE=real python -m scripts.run_paper_trading --dry-run`

| 구분 | 도메인 | 매수 TR_ID | 매도 TR_ID | 잔고 TR_ID |
|---|---|---|---|---|
| 모의 | openapivts...:29443 | VTTC0802U | VTTC0801U | VTTC8434R |
| 실전 | openapi...:9443 | TTTC0802U | TTTC0801U | TTTC8434R |

> 실전 전환 전 체크리스트: ① 백테스트 OOS 검증 ② 모의투자 1~3개월 운용 ③ 소액부터 시작 ④ 손절/포지션 상한 동작 확인.

---

## 6. 면책

본 코드는 교육·연구 목적의 참고 구현입니다. 투자 자문이 아니며, 백테스트 성과가 미래 수익을 보장하지 않습니다.
실거래로 인한 모든 손익의 책임은 사용자에게 있습니다.
