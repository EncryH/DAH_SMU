import time
from pymavlink import mavutil

UAV_HOST   = '172.20.0.10'
UAV_PORT   = 14551
GCS_SYS_ID = 255


def send_rtl(mav):
    mav.mav.command_long_send(
        target_system=1,
        target_component=1,
        command=mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
        confirmation=0,
        param1=0, param2=0, param3=0, param4=0,
        param5=0, param6=0, param7=0,
    )
    print(f"[RESPONDER] RTL 명령 전송 완료 → UAV {UAV_HOST}:{UAV_PORT}")


def send_safe_mode(mav):
    mav.mav.set_mode_send(
        target_system=1,
        base_mode=mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED,
        custom_mode=0,
    )
    print(f"[RESPONDER] 안전 모드 전환 완료 → UAV {UAV_HOST}:{UAV_PORT}")


def respond(threats, send_event=None):
    if not threats:
        return

    mav = mavutil.mavlink_connection(
        f'udpout:{UAV_HOST}:{UAV_PORT}',
        source_system=GCS_SYS_ID,
    )

    for threat in threats:
        print(f"[RESPONDER] 위협 대응 시작")
        print(f"[RESPONDER] 원인: {threat['reason']}")

        if 'cmd' in threat and threat['cmd'] == mavutil.mavlink.MAV_CMD_NAV_LAND:
            print(f"[RESPONDER] LAND 주입 탐지 → RTL 명령으로 대응")
            send_rtl(mav)
            if send_event:
                send_event("RESPONDER",
                           "RTL 명령으로 대응",
                           level="warn",
                           detail=f"LAND 주입 차단 → RTL 전송 (SYS_ID={GCS_SYS_ID})",
                           status="BLOCKED")

        elif threat['reason'] == 'Replay Attack 탐지 (SEQ 역전)':
            print(f"[RESPONDER] Replay Attack 탐지 → 안전 모드 전환")
            send_safe_mode(mav)
            if send_event:
                send_event("RESPONDER",
                           "안전 모드 전환",
                           level="warn",
                           detail=f"Replay Attack 차단 → SAFE_MODE 전송",
                           status="BLOCKED")

        else:
            print(f"[RESPONDER] 알 수 없는 위협 → RTL 명령으로 대응")
            send_rtl(mav)
            if send_event:
                send_event("RESPONDER",
                           "RTL 명령으로 대응",
                           level="warn",
                           detail=threat.get('reason', '알 수 없는 위협'),
                           status="BLOCKED")

        time.sleep(1)
