import json
import time
from datetime import datetime
import subprocess
from pathlib import Path
from sendMail import *

LAST_MENU_FILE = Path("/run/2506_music/last-menu")
CHECK_INTERVAL = 15 
TIMEOUT = 300

last_sw_epoch = time.time()

def check_sw(flag_skip):
    global last_sw_epoch

    try:
        with open(LAST_MENU_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        menu = str(data.get("menu", ""))
        dt_epoch = float(data.get("dt_epoch", 0))

        # menuに "sw" または "signal" が含まれている場合
        if "sw" in menu or "signal" in menu:
            if last_sw_epoch is None or dt_epoch > last_sw_epoch:
                last_sw_epoch = dt_epoch
            print("menu: ", menu, " -- epoch: ", int(last_sw_epoch), " -- menuからの秒数: ", int(time.time() - last_sw_epoch))

    except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
        print(f"ファイル読み取りエラー: {e}")

    if last_sw_epoch is None:
        return False

    if flag_skip == True:
        return True

    # 最後のsw記録から120秒未満ならTrue
    return 0 <= time.time() - last_sw_epoch < TIMEOUT


if __name__ == "__main__":
    # CHECK_INTERVAL秒ごとに判定
    flag_skip = False # skip if flag is True
    last_alert_epoch = None

    while True:
        hour = datetime.now().hour

        if 16 <= hour < 22:
            result = check_sw(flag_skip)
            #print(f"SW状態: {result}")

            if result == False:
                print(f"SW {TIMEOUT} 秒反応なし")

                log = subprocess.run(
                    ["tail", "-n", "30", "/opt/2506_music/log/status.log"],
                    capture_output=True,
                    text=True
                    )
                
                print(log.stdout)
                sendMail(["nada@zigenls.com","motoharu.ando@s-imf.co.jp"], "TIMEOUT_札幌SW", log.stdout, 1)
                #sendMail(["nada@zigenls.com"], "TIMEOUT_札幌SW", log.stdout, 1)
                #sendMail(["motoharu.ando@s-imf.co.jp"], "TIMEOUT_札幌SW", log.stdout, 1)
                flag_skip = True 
                last_alert_epoch = time.time()

                time.sleep(TIMEOUT)

        time.sleep(CHECK_INTERVAL)

        if flag_skip == True and time.time() > last_alert_epoch + 180 :
            flag_skip = False 
