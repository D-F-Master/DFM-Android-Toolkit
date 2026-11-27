#!/system/bin/sh
mkdir -p '/data/media/0/Android/data/com.dfm.probe/files/cam_in'
cp '/data/local/tmp/cam_in/d9ed7e09-119f-41be-a4ad-f4ef2243035b.jpg' '/data/media/0/Android/data/com.dfm.probe/files/cam_in/d9ed7e09-119f-41be-a4ad-f4ef2243035b.jpg' && rm -f '/data/local/tmp/cam_in/d9ed7e09-119f-41be-a4ad-f4ef2243035b.jpg' || { echo COPY_FAIL; exit 1; }
O=$(stat -c %u /data/data/com.dfm.probe 2>/dev/null)
[ -z "$O" ] && O=$(stat -c %u '/data/media/0/Android/data/com.dfm.probe/files' 2>/dev/null)
chown -R "$O" '/data/media/0/Android/data/com.dfm.probe/files/cam_in'
chmod 2770 '/data/media/0/Android/data/com.dfm.probe/files/cam_in'
chmod 664 '/data/media/0/Android/data/com.dfm.probe/files/cam_in/d9ed7e09-119f-41be-a4ad-f4ef2243035b.jpg'
echo PUSH_OK
