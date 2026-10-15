"""隨需啟動 (launchd socket activation) 與閒置自動結束: 沒在用時不佔記憶體。

launchd 代為監聽 127.0.0.1:8765, 有連線進來才啟動程式並把監聽中的 socket 交給它 (activated_socket);
程式閒置 (IDLE 秒沒有請求、沒有更新在跑、也沒有到點的排程更新) 就自己結束, 下一個連線再由 launchd 啟動。
週六的排程更新由另一個 launchd 定時工作敲一下 /api/status 把程式叫醒, 程式醒著時看門狗會把到點的更新做完才結束。
"""
import ctypes
import socket
import time

from investor.web import autorefresh, jobs

IDLE = 15 * 60          # 秒; 多久沒有請求就結束
CHECK = 60              # 秒; 看門狗多久檢查一次

_last_seen = time.time()


def touch():
    """每個請求都呼叫: 記下最後一次有人用的時間。"""
    global _last_seen
    _last_seen = time.time()


def activated_socket(name="Listeners"):
    """取得 launchd 代為監聽的 socket (plist 的 Sockets/<name>); 不是由 launchd 以 socket 啟動時回傳 None。"""
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    except OSError:
        return None
    fds, cnt = ctypes.POINTER(ctypes.c_int)(), ctypes.c_size_t(0)
    if libc.launch_activate_socket(name.encode(), ctypes.byref(fds), ctypes.byref(cnt)) != 0 or cnt.value == 0:
        return None
    fd = fds[0]
    libc.free(fds)
    return socket.socket(fileno=fd)


def watchdog(srv, idle=IDLE, check=CHECK, exit_when_idle=True):
    """背景執行: 定期做到點的排程更新; exit_when_idle 時, 閒置就關掉伺服器 (serve_forever 返回, 程式結束)。"""
    while True:
        try:
            started = autorefresh.scheduled_start()
            if started:
                print(f"排程更新: {started[0]} ({started[1]})", flush=True)
        except Exception as e:                       # 排程失敗不能讓伺服器掛掉, 下次檢查再試
            started = None
            print(f"排程更新檢查失敗: {e}", flush=True)
        if exit_when_idle and not started and not jobs.job["running"] and time.time() - _last_seen > idle:
            print(f"閒置 {idle // 60} 分鐘, 結束 (下次有連線時 launchd 會再啟動)", flush=True)
            srv.shutdown()
            return
        time.sleep(check)
