import sys, time, json
sys.path.insert(0, '.')
import asyncio
import websockets

async def test():
    uri = 'ws://127.0.0.1:3000/ws/remote'
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({'type':'start','devices':['202600298120','192.168.0.4:5555'],'source':'202600298120'}))
        ready = False
        t0 = time.time()
        while not ready and time.time() - t0 < 40:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                if isinstance(raw, bytes):
                    # binary 视频帧，跳过
                    continue
                m = json.loads(raw)
                t = m.get('type')
                if t == 'meta':
                    print('meta:', m['deviceId'], m.get('width'), 'x', m.get('height'))
                elif t == 'error':
                    print('error:', m.get('deviceId'), m.get('message'))
                elif t == 'ready':
                    print('ready:', [d['deviceId'] for d in m.get('devices', [])])
                    ready = True
            except asyncio.TimeoutError:
                print('recv timeout')
                break
        if not ready:
            print('NO READY - abort')
            return
        ev = {'kind':'touch','action':0,'x':640,'y':400,'w':1280,'h':720}
        await ws.send(json.dumps({'type':'mirror','event':ev}))
        await ws.send(json.dumps({'type':'mirror','event':{**ev,'action':1}}))
        print('mirror touch sent (expect click on 192.168.0.4)')
        await asyncio.sleep(3)
        print('done')

asyncio.run(test())
