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

```text
┌────────────────────── 무인체계 UAV / UGV ──────────────────────────────────
│                                                                          │
│  ┌────────────── UAV ──────────────┐ ┌────────────── UGV ──────────────┐ │
│  │ Autopilot / Flight Controller   │ │ Vehicle Controller              │ │
│  │ Companion Computer              │ │ Onboard / Mission Computer      │ │
│  │ - MAVLink Telemetry             │ │ - ROS2 / MQTT Telemetry         │ │
│  │ - Payload Status                │ │ - Sensor Status                 │ │
│  │ - Command Receive / Execute     │ │ - Command Receive / Execute     │ │
│  └──────────────┬──────────────────┘ └───────────────┬─────────────────┘ │
│                 │                                    │                   │
└─────────────────┼────────────────────────────────────┼───────────────────┘
                  │ Telemetry                          │ Telemetry
                  │ Telemetry                          │ Telemetry 
                  ▼                                    ▼
            ┌────────────────────────────────────────────────┐
            │                Tactical Router                 │
            │                전술 통신 라우터                  │
            │ - Telemetry 수집 / 분배                         │
            │ - Command 라우팅                                │
            │ - UAV / UGV / GCS / C2 연결                     │
            └──────────────────────┬─────────────────────────┘
                                   │
        ┌───────────────┬──────────┴───────────┬────────────────┐
        ▼               ▼                      ▼                ▼
┌───────────────┐ ┌──────────────────────┐ ┌───────────────┐ ┌──────────────────────────┐
│ GCS           │ │ Mission Control / C2 │ │ Dashboard     │ │ Telemetry Collector      │
│ Ground Control│ │ Server               │ │ 상태 시각화    │ │ Raw 로그 수집 / 저장      │
│ Station       │ │ - 상태 수신           │ └───────────────┘ └────────────┬─────────────┘
│ - 상태 확인    │ │ - 임무 판단          │                                ▼
│ - 수동 조작    │ │ - Command 생성       │                             Log DB
│ - 명령 입력    │ │ - 상위 C2 연동       │
└───────┬───────┘ └──────────┬───────────┘
        │                    │
        │                    │ 전술망 연동 데이터
        │                    │ 상태 / 위치 / 임무 / 표적 / 영상 메타데이터
        │                    ▼
        │             ┌──────────────────────┐
        │             │        TMMR          │
        │             │ 전술 다대역 다기능 무전기│
        │             │ - TICN 접속 장비      │
        │             │ - 음성 / 데이터 송수신 │
        │             │ - 전술 무선 노드       │
        │             └──────────┬───────────┘
        │                        │
        │                        │ TICN 전술망 연동
        │                        ▼
        │             ┌──────────────────────┐
        │             │        TICN          │
        │             │ 전술정보통신망         │
        │             │ - 전술 데이터망        │
        │             │ - 지휘통제망 연동      │
        │             │ - 부대/차량/지휘소 연결 │
        │             └──────────┬───────────┘
        │                        │
        │                        │ 상위 지휘통제 연동
        │                        ▼
        │             ┌──────────────────────┐
        │             │   상위 C2 / BMS       │
        │             │ 지휘통제 / 전투관리체계 │
        │             │ - 작전 상황 공유       │
        │             │ - 표적 / 좌표 공유     │
        │             │ - 상급부대 명령 하달   │
        │             └──────────────────────┘
        │
        └──────────┬─────────┘
                   │ Command
                   ▼
            ┌────────────────┐
            │ Tactical Router│
            └───────┬────────┘
                    │
       ┌────────────┴────────────┐
       ▼                         ▼
Companion Computer          Onboard Computer
       ▼                         ▼
Autopilot / FC              Vehicle Controller
       ▼                         ▼
      UAV                       UGV
```

Recon, Executor, Defense는 별도의 직접 공격/방어 실습 레이어로 유지합니다.

## 구현 범위

본 프로젝트는 실제 TICN/SATCOM 또는 실제 MAVLink/ROS2 네트워크를 완전 구현한 것이 아니라,
UAV/UGV 전술 통신 구조를 학습하고 시연하기 위한 Docker 기반 시뮬레이션이다.

현재 Telemetry는 MAVLink/ROS2/MQTT 메시지 구조를 모사한 JSON 기반 데이터로 생성되며,
Tactical Router는 이를 Mission Control, Dashboard, Telemetry Collector로 분배한다.

Command는 Mission Control 또는 GCS에서 생성되어 Tactical Router를 통해 UAV/UGV 시뮬레이터로 전달되며,
수신된 명령은 이후 Telemetry 상태 변화로 반영된다.

## 시스템 구성 요소

- UAV / UGV는 상태 정보를 생성한다.
- Companion / Onboard Computer는 상태 정보를 수집한다.
- Tactical Router는 Telemetry와 Command를 중계한다.
- Mission Control / C2 Server는 상태를 판단하고 Command를 생성한다.
- GCS는 운용자가 상태를 확인하고 명령을 입력하는 지상통제소이다.
- Dashboard는 상태, 링크, 로그, Agent 판단 결과를 시각화한다.
- Telemetry Collector는 Raw 로그를 수집한다.
- Log DB는 통신 및 판단 이력을 저장한다.
- AI Agent는 로그와 상태 정보를 기반으로 판단 흐름을 생성한다.

## 실행

```powershell
docker compose up -d --build dah-dashboard
```

```text
Dashboard: http://localhost:8081
Mission Control API: http://localhost:8082/api/dashboard
```
