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
+------------------------------- UAV / UGV Asset Layer ------------------------------+
|                                                                                   |
|  +---------- UAV Simulator -----------+   +--------- UGV Simulator ----------+   |
|  | - Autopilot / FC Logic             |   | - Vehicle Controller Logic       |   |
|  | - MAVLink-like Telemetry / Command |   | - ROS2/MQTT-like Telemetry       |   |
|  | - Payload Status                   |   | - Sensor Status                  |   |
|  | - Command Receive / Execute        |   | - Command Receive / Execute      |   |
|  +------------------+-----------------+   +-----------------+----------------+   |
+---------------------+-------------------------------------------+---------------+
                      | C2 Data Link (Telemetry/Report down)      | C2 Data Link
                      | Command / Tasking up                      | Command / Tasking up
                      v                                           v

+-----------------------------------------------------------------------------------+
| GCS / Ground Gateway / Mission Control Server                                     |
| - UAV / UGV Telemetry receive & parse                                             |
| - Mission status decision                                                         |
| - Manual control / Command generation                                             |
| - Convert Upper C2/BMS orders to UAV/UGV Commands                                |
| - Tactical net message mapping: position / status / mission / target / video meta |
+---------------+---------------------+---------------------+-----------------------+
                |                     |                     |
                v                     v                     v
    +-------------------+   +--------------------+   +---------------------+
    | Dashboard         |   | Telemetry          |   | AI Defense Agent    |
    | - Status/map view |   | Collector / LogDB  |   | - Realtime analysis |
    | - Mission display |   | - Telemetry Log    |   | - Command integrity |
    | - Alert display   |   | - Command Log      |   | - Anomaly detection |
    | - Attack/defense  |   | - Network Log      |   | - Response decision |
    +-------------------+   | - Attack Log       |   +----------+----------+
                            +--------------------+              |
                                                                v
                                                   Alert / Block / Quarantine
                                                   Re-route / Fallback / Review
                ^
                | Controlled attack event injection
+---------------+-------------------------------------------------------------------+
| AI Attack Agent                                                                   |
| - Auto attack event generation inside Docker virtual network                     |
| - Telemetry forgery / Command tampering / GPS anomaly injection                   |
| - Comm delay / loss / block / manipulation events                                 |
| - AI Defense Agent detection performance validation                               |
| * Operates only inside closed UAV/UGV domain virtual environment                 |
+-----------------------------------------------------------------------------------+

                      |
                      | Tactical net data
                      | Report / Situation Data (down)
                      | Command / Tasking (up)
                      v

+-----------------------------------------------------------------------------------+
| Virtual Tactical Router / TIPS                                                    |
| - Docker Network based virtual tactical router                                    |
| - IP packet routing between GCS and tactical network                              |
| - Delay / loss / block / tamper event injection point                             |
| - QoS / priority processing simulation                                            |
| - Relay tactical net data converted by GCS                                        |
| * Does not parse MAVLink / ROS2 directly                                          |
+----------------------+------------------------------------------------------------+
                       | Report / Situation Data (down) / Command / Tasking (up)
                       v

+-----------------------------------------------------------------------------------+
| TMMR / Tactical Radio (CNRS-series)                                               |
| - Tactical radio node                                                             |
| - Voice / data transceiver                                                        |
| - TICN access segment                                                             |
| - Tactical radio link simulation                                                  |
+----------------------+------------------------------------------------------------+
                       | Report / Situation Data (down) / Command / Tasking (up)
                       v

+-----------------------------------------------------------------------------------+
| TICN-like Tactical Network                                                        |
| - Tactical information communication network simulation                           |
| - Tactical data network                                                           |
| - C4ISR / command & control network flow simulation                               |
| - Connect field tactical nodes to upper command system                            |
+----------------------+------------------------------------------------------------+
                       | Report / Situation Data (down) / Command / Tasking (up)
                       v

+-----------------------------------------------------------------------------------+
| Upper C2 / BMS Simulator                                                          |
| - Operational situation sharing                                                   |
| - Target / coordinate sharing                                                     |
| - Surveillance zone assignment                                                    |
| - Mission change order                                                            |
| - Higher command dissemination                                                    |
| * Does not command UAV/UGV directly -- converted to Commands via GCS              |
+-----------------------------------------------------------------------------------+
```

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
