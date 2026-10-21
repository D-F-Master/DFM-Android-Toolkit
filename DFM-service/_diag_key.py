import sys, time, subprocess, threading
sys.path.insert(0, '.')
from routes.batch_remote import DeviceSession, get_adb_path

adb = get_adb_path()
logcat = subprocess.Popen(f'"{adb}" -s 202600298120 logcat -T 1 -s scrcpy', shell=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding='utf-8', errors='replace')
lines = []
def reader():
    for line in logcat.stdout:
        lines.append(line.rstrip())
threading.Thread(target=reader, daemon=True).start()

s = DeviceSession('202600298120', 29983)
ok = s.start()
print('session:', ok)
if ok:
    time.sleep(1.5)
    print('inject BACK down+up')
    s.inject_keycode(4, 0)
    s.inject_keycode(4, 1)
    time.sleep(2)
    print('running:', s.running)
s.stop()
logcat.kill()
time.sleep(0.5)
print('--- scrcpy logcat ---')
for l in lines[-15:]:
    print(l)
