"""临时诊断脚本：测 smtp.163.com 各端口的 TCP 连通 + SSL/STARTTLS 握手（不登录）"""
import smtplib
import socket

host = "smtp.163.com"
for port in [465, 994, 587, 25]:
    # TCP 连通性
    try:
        s = socket.create_connection((host, port), timeout=10)
        s.close()
        tcp = "OK"
    except Exception as e:
        tcp = f"FAIL {type(e).__name__}"

    # SSL / STARTTLS 握手（不 login，排除授权码干扰）
    handshake = "-"
    try:
        if port in (465, 994):
            with smtplib.SMTP_SSL(host, port, timeout=15) as srv:
                handshake = f"SSL握手OK(noop={srv.noop()[0]})"
        else:
            with smtplib.SMTP(host, port, timeout=15) as srv:
                srv.starttls()
                handshake = f"STARTTLS_OK(noop={srv.noop()[0]})"
    except Exception as e:
        handshake = f"FAIL {type(e).__name__}: {e}"

    print(f"port {port}: TCP={tcp} | {handshake}")
