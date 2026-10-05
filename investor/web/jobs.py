"""背景工作: 一次只跑一個, 依序執行步驟; 每個步驟成功就先存檔 (後面失敗也不會丟), 失敗不會中止其餘步驟。"""
import threading
import time

from investor.navellier import settings
from investor.web import store, steps

job = {"running": False, "task": "", "label": "", "step": "", "started": 0, "finished": 0, "error": None}
_lock = threading.Lock()


def run_job(task: str) -> None:
    label, modules = steps.TASKS[task]
    cached = task == "init"
    errors = []
    uni = settings.load_mode()         # 頁首選的股票池; 工作開始時鎖定, 中途切換不影響這次更新
    if uni == "watchlist":             # 自訂觀察清單: 跌幅/本益比/淨值比/殖利率/神奇公式不適用
        modules = [m for m in modules if m not in steps.POOL_SCREENS]
    n = len(modules) + 1
    for i, m in enumerate(modules, 1):
        name, fn = steps.STEPS[m]
        job["step"] = f"{i}/{n} {name}"
        try:
            fn(cached, uni)
            with store.lock:
                store.save()
        except Exception as e:
            errors.append(f"{name}: {str(e)[:300]}")
    job["step"] = f"{n}/{n} 計算 1M/3M/6M/1Y 股價表現"
    try:
        steps.do_perf(cached, uni)
        with store.lock:
            store.save()
    except Exception as e:
        errors.append(f"股價表現: {str(e)[:300]}")
    job.update(running=False, finished=time.time(), error=" ｜ ".join(errors) or None, step="")


def start_job(task: str) -> bool:
    """啟動背景工作; 已有工作在跑則回傳 False。"""
    with _lock:
        if job["running"]:
            return False
        job.update(running=True, task=task, label=steps.TASKS[task][0], step="準備中", started=time.time(), error=None)
    threading.Thread(target=run_job, args=(task,), daemon=True).start()
    return True
