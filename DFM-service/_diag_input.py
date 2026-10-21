import sys, time, subprocess, threading
sys.path.insert(0, '.')
from routes.batch_remote import DeviceSession, get_adb_path

adb = get_adb_path()

# 同时抓 input 相关日志和 scrcpy 日志
logcat = subprocess.Popen(f'"{adb}" -s 202600298120 logcat -T 1', shell=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding='utf-8', errors='replace')
lines = []
def reader():
    for line in logcat.stdout:
        low = line.lower()
        if 'scrcpy' in low or 'inject' in low or 'inputdispatcher' in low:
            lines.append(line.rstrip())
threading.Thread(target=reader, daemon=True).start()

s = DeviceSession('202600298120', 29983)
ok = s.start()
print('session:', ok)
if ok:
    time.sleep(2)
    print('inject BACK')
    s.inject_keycode(4, 0)
    s.inject_keycode(4, 1)
    time.sleep(2)
    print('inject touch center')
    w, h = s.video_width, s.video_height
    s.inject_touch(0, w/2, h/2, w, h)
    time.sleep(0.1)
    s.inject_touch(1, w/2, h/2, w, h)
    time.sleep(2)
    print('running:', s.running)
s.stop()
logcat.kill()
time.sleep(0.5)
print('--- filtered logcat ---')
for l in lines[-30:]:
    print(l)
