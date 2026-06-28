# DAH 2026 - UAV/UGV  통신 시뮬레이션

> **도메인**: UAV / UGV  
> **환경**: 위성 네트워크 기반 클라우드 가상 전장  

---

## 프로젝트 개요

DAH - **UAV/UGV 전술 무인체계 통신 구조 시뮬레이션**입니다.

LIG Defense&Aerospace의 항공전자·드론, 전자전, 무인화·미래전 분야와  
한화시스템의 C5I, TICN, 군 위성통신체계-II, 전술데이터링크 개념을 참고합니다.

현재 대시보드는 C2, Mission Control, UAV, UGV, EW UAV, TICN/SATCOM 링크 상태를 움직이는 전장 시뮬레이션 형태로 시각화합니다.

## 아키텍처

Docker 기반 UAV/UGV 도메인 가상 환경에서 무인체계의 Telemetry/Command 흐름을 구성하고, 그 위에서 AI Attack Agent와 AI Defense Agent의 자동 공격·방어 성능을 검증하는 아키텍처.

```text
┌──────────────────────────── UAV / UGV Asset Layer ─────────────────────────────┐
│                                                                                │
│  ┌────────────────── UAV Simulator ──────────────────┐ ┌──── UGV Simulator ──┐ │
│  │ Autopilot / Flight Controller                      │ │ Vehicle Controller  ││
│  │ - Flight Control Logic                             │ │ - Mobility Control  ││
│  │ - Mission Command Execute                          │ │ - Command Execute   ││
│  │                                                    │ │                     ││
│  │ Companion Computer                                 │ │ Onboard / Mission   ││
│  │ - MAVLink-like Telemetry / Command                 │ │ Computer            ││
│  │ - Payload Status                                   │ │ - ROS2/MQTT-like    ││
│  │ - GCS Communication                                │ │   Telemetry         ││
│  │ - Command Receive / Forward                        │ │ - Sensor Status     ││
│  │                                                    │ │ - GCS Communication ││
│  └──────────────────────┬─────────────────────────────┘ └─────────┬──────────┘ │
└─────────────────────────┼─────────────────────────────────────────┼────────────┘
                          │ C2 Data Link                            │ C2 Data Link
                          │ Telemetry / Report ↓                    │ Telemetry / Report ↓
                          │ Command / Tasking ↑                     │ Command / Tasking ↑
                          ▼                                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ GCS / Ground Gateway / Mission Control Server                                 │
│ - UAV / UGV Telemetry 수신 및 해석                                             │
│ - 임무 상태 판단                                                               │
│ - 수동 조작 / Command 생성                                                     │
│ - Upper C2/BMS 명령 → UAV/UGV Command 변환                                    │
│ - 전술망 메시지 변환: 위치 / 상태 / 임무 / 표적 / 영상 메타데이터              │
└───────────────┬──────────────────────┬──────────────────────┬────────────────┘
                │                      │                      │
                ▼                      ▼                      ▼
   ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐
   │ Dashboard          │  │ Telemetry           │  │ AI Defense Agent     │
   │ - 상태/지도 시각화  │  │ Collector / LogDB  │  │ - 실시간 상태 분석    │
   │ - 임무 표시         │  │ - Telemetry Log     │  │ - Command 무결성 검증│
   │ - 경고 표시         │  │ - Command Log       │  │ - 이상징후 탐지      │
   │ - 공격/방어 결과    │  │ - Network/Attack Log│  │ - 대응 정책 결정     │
   └────────────────────┘  └─────────────────────┘  └──────────┬───────────┘
                                                                │
                                                                ▼
                                                   Alert / Block / Quarantine
                                                   Re-route / Fallback / Review

               ▲
               │ 통제된 공격 이벤트 주입
┌──────────────┴──────────────────────────────────────────────────────────────┐
│ AI Attack Agent                                                             │
│ - Docker 가상 네트워크 내부 자동 공격 이벤트 생성                            │
│ - Telemetry 위조 / Command 변조 / GPS 이상 좌표 주입                         │
│ - 통신 지연 / 손실 / 차단 / 변조 이벤트                                      │
│ - AI Defense Agent 탐지 성능 검증                                            │
│ ※ 폐쇄형 UAV/UGV 도메인 가상 환경 내부에서만 동작                            │
└─────────────────────────────────────────────────────────────────────────────┘

                          │
                          │ 전술망 연동 데이터
                          │ Report / Situation Data ↓
                          │ Command / Tasking ↑
                          ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ Virtual Tactical Router / TIPS                                                │
│ - Docker Network 기반 가상 전술 라우터                                         │
│ - GCS / 전술망 간 IP 패킷 라우팅                                               │
│ - 지연 / 손실 / 차단 / 변조 이벤트 적용 지점                                   │
│ - QoS / 우선순위 처리 모사                                                     │
│ - GCS가 변환한 전술망 데이터 중계                                              │
│ ※ MAVLink / ROS2 직접 해석 없음                                               │
└────────────────────────┬──────────────────────────────────────────────────────┘
                         │ Report / Situation Data ↓
                         │ Command / Tasking ↑
                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ TMMR / 전투무선체계 (CNRS-series)                                              │
│ - 전술 무선 노드                                                               │
│ - 음성 / 데이터 송수신                                                         │
│ - TICN 접속 구간                                                               │
│ - 전술 무선 링크 모사                                                          │
└────────────────────────┬──────────────────────────────────────────────────────┘
                         │ Report / Situation Data ↓
                         │ Command / Tasking ↑
                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ TICN-like Tactical Network                                                    │
│ - 전술정보통신망 모사                                                          │
│ - 전술 데이터망                                                               │
│ - C4ISR / 지휘통제망 연동 흐름 모사                                            │
│ - 현장 전술 노드와 상위 지휘체계 연결                                          │
└────────────────────────┬──────────────────────────────────────────────────────┘
                         │ Report / Situation Data ↓
                         │ Command / Tasking ↑
                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ Upper C2 / BMS Simulator                                                      │
│ - 작전 상황 공유                                                              │
│ - 표적 / 좌표 공유                                                            │
│ - 감시 구역 지정                                                              │
│ - 임무 변경 지시                                                              │
│ - 상급부대 명령 하달                                                          │
│ ※ UAV/UGV 직접 명령 없음 — GCS 경유하여 Command로 변환                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

## 구현 범위

본 프로젝트는 실제 TICN, TMMR, MAVLink, ROS2 네트워크를 완전 구현하는 것이 아니라, **Docker 기반 UAV/UGV 도메인 가상 환경에서 Telemetry/Command 흐름과 AI 공격·방어 구조를 검증하기 위한 통신 시뮬레이션**이다.

UAV는 Autopilot/Flight Controller와 Companion Computer 구조를 모사한다. Autopilot/FC는 비행 제어와 Mission Command 실행을 담당하고, Companion Computer는 MAVLink-like Telemetry/Command, Payload Status, GCS 통신을 담당한다.

UGV는 Vehicle Controller와 Onboard/Mission Computer 구조를 모사하며, ROS2/MQTT-like Telemetry와 Sensor Status를 생성한다.

Telemetry는 실제 MAVLink/ROS2/MQTT 패킷이 아니라, 해당 메시지 구조를 참고한 JSON 기반 데이터로 생성된다. UAV/UGV의 Telemetry는 C2 Data Link를 통해 GCS / Mission Control Server로 전달된다.

GCS는 Telemetry를 수신·해석하고 Dashboard, Telemetry Collector/LogDB, AI Defense Agent로 데이터를 분기한다. 또한 위치, 상태, 임무, 표적, 영상 메타데이터를 전술망 연동 메시지로 변환하여 Virtual Tactical Router/TIPS로 전달한다.

Command는 GCS 운용자 또는 Upper C2/BMS Simulator에서 생성된다. Upper C2/BMS의 명령은 TICN-like Network, TMMR, Virtual Tactical Router/TIPS를 거쳐 GCS로 전달되고, GCS에서 UAV/UGV가 실행 가능한 Command로 변환된 뒤 C2 Data Link를 통해 하달된다.

AI Attack Agent는 폐쇄형 Docker 가상 네트워크 내부에서 Telemetry 위조, Command 변조, GPS 이상 좌표, 통신 지연·손실·차단·변조 이벤트를 생성한다. AI Defense Agent는 실시간 Telemetry, Command Flow, Network Event, Mission State를 분석해 이상징후를 탐지하고 대응 정책을 결정한다.

## 시스템 구성 요소

- **UAV Simulator**
  - Autopilot/FC: 비행 제어, Mission Command 실행
  - Companion Computer: MAVLink-like Telemetry/Command, Payload Status, GCS 통신

- **UGV Simulator**
  - Vehicle Controller: 주행 제어, Command 실행
  - Onboard/Mission Computer: ROS2/MQTT-like Telemetry, Sensor Status, GCS 통신

- **C2 Data Link**
  - UAV/UGV와 GCS 사이의 Telemetry/Command 통신 구간
  - Telemetry/Report: UAV/UGV → GCS
  - Command/Tasking: GCS → UAV/UGV

- **GCS / Ground Gateway / Mission Control Server**
  - UAV/UGV Telemetry 수신·해석
  - 임무 상태 판단 및 Command 생성
  - Upper C2/BMS 명령을 UAV/UGV용 Command로 변환
  - 전술망 연동 메시지 생성

- **Dashboard**
  - UAV/UGV 상태, 지도, 임무, 경고, 공격/방어 결과 시각화

- **Telemetry Collector / LogDB**
  - Telemetry Log, Command Log, Network Log, Attack Log 저장

- **AI Attack Agent**
  - 폐쇄형 Docker 가상망 내부에서 통제된 공격 이벤트 생성
  - Telemetry 위조, Command 변조, GPS 이상 좌표, 통신 지연·손실·차단·변조 이벤트 수행

- **AI Defense Agent**
  - 실시간 Telemetry, Command Flow, Network Event, Mission State 분석
  - Command 무결성 검증, 이상징후 탐지, 공격 유형 분류, 대응 정책 결정

- **Virtual Tactical Router / TIPS**
  - Docker Network 기반 가상 전술 라우터
  - GCS와 전술망 사이의 IP 패킷 라우팅 및 전술망 데이터 중계
  - 지연, 손실, 차단, 변조 이벤트 적용 지점
  - MAVLink/ROS2 직접 해석 없음

- **TMMR / 전투무선체계(CNRS-series)**
  - Tactical Router/TIPS와 TICN-like Network 사이의 전술 무선 접속 구간 모사

- **TICN-like Tactical Network**
  - 전술정보통신망 데이터 전달 흐름 모사
  - C4ISR 지휘통제망 연동 흐름 표현

- **Upper C2 / BMS Simulator**
  - 작전 상황 공유, 표적/좌표 공유, 감시 구역 지정, 임무 변경 지시
  - UAV/UGV에 직접 명령하지 않고 GCS를 통해 Command로 변환

## 네트워크 구성

### 내부 UDP 포트

| 구간 | 포트 | 프로토콜 | 의미 |
| --- | ---: | --- | --- |
| UAV → Companion | `14550` | UDP / MAVLink-like | UAV Telemetry 전송 |
| Companion → UAV | `14551` | UDP / MAVLink-like | UAV Command 전달 |
| GCS → Companion | `14552` | UDP / JSON | Companion Command 수신 |
| Companion → GCS | `14555` | UDP / JSON | MAVLink-like Telemetry를 JSON으로 변환 후 GCS 전달 |
| GCS → Tactical Router | `14560` | UDP / JSON | GCS가 변환한 전술망 연동 데이터 전달 |
| Router → Upper C2/BMS | `14545` | UDP / JSON | 전술 상황 데이터 전달 |
| Upper C2/BMS → Router | `14546` | UDP / JSON | 상위 C2 작전 명령 하달 |
| Router → GCS | `14562` | UDP / JSON | Upper C2 명령을 GCS로 전달 |
| UGV → Router | `14660` | UDP / JSON | UGV Telemetry 전달 |
| Router → UGV | `14661` | UDP / JSON | UGV Command 전달 |
| Attack/Jam Event → Router | `14590` | UDP / JSON | Jamming/Event 입력 |
| GCS → Collector | `14541` | UDP / JSON | Telemetry / Command / Attack Log 수집 |
| GCS/Router → Dashboard | `14571` | UDP / JSON | Dashboard Telemetry 수신 |

### 외부 접속 포트

| 서비스 | URL | 프로토콜 | 의미 |
| --- | --- | --- | --- |
| API Gateway / Dashboard | `http://localhost:9000` | HTTP/TCP | 메인 Dashboard |
| GCS API | `http://localhost:9000/gcs/` | HTTP/TCP | GCS 상태/API |
| Upper C2/BMS API | `http://localhost:9000/c2/` | HTTP/TCP | Upper C2/BMS 상태/API |
| Tactical Router API | `http://localhost:9000/router/` | HTTP/TCP | Tactical Router 상태/API |
| Tactical Router Direct API | `http://localhost:8084` | HTTP/TCP | Router 상태 직접 확인 |

## 실행

```powershell
docker compose up -d --build dah-dashboard
```

```text
Dashboard: http://localhost:9000
Mission Control API: http://localhost:9000/api/dashboard
```
