import json, re, subprocess, time, urllib.request
from collections import Counter
MON = '/data/pi5-mon'


def get(u):
    try:
        with urllib.request.urlopen(u, timeout=2) as r:
            return json.load(r)
    except Exception:
        return {}


while True:
    ts = time.time()
    iso = time.strftime('%H:%M:%S', time.localtime(ts)) + ('.%03d' % ((ts % 1) * 1000))
    r = get('http://localhost:3000/api/v1/getState')
    s = get('http://127.0.0.1:9879/status')
    t = s.get('track') or {}
    try:
        m = subprocess.run(['mpc', 'status'], capture_output=True, text=True, timeout=3).stdout.splitlines()
    except Exception:
        m = []
    mpc = m[1].strip() if len(m) > 1 else ''

    try:
        out = subprocess.run(['sudo', '-n', '/usr/bin/find', '/proc', '-maxdepth', '3',
                              '-path', '/proc/[0-9]*/fd/*', '-lname', '*fusiondspfifo*',
                              '-printf', '%p\n'],
                             capture_output=True, text=True, timeout=5).stdout
        h = []
        for line in out.split():
            pid = line.split('/')[2]
            try:
                h.append(open('/proc/%s/comm' % pid).read().strip())
            except Exception:
                h.append('?')
        fifo = dict(Counter(h))
    except Exception:
        fifo = {}

    # Compact signature: this is what the screen capture is triggered on.
    # mpc's second line carries a LIVE position, e.g. "[playing] #1/1   0:08/2:49 (4%)",
    # which changes every second. Using it verbatim defeats change-triggering and captures
    # a frame on every tick. Keep only the stable part: the state word and queue index.
    mm = re.match(r'\[(\w+)\]\s*(#\d+/\d+)?', mpc)
    mpc_state = (mm.group(1) + (mm.group(2) or '')) if mm else ('none' if not mpc else 'other')

    sig = '%s/%s/vol=%s|sp=%s/%s/%s|mpc=%s|fifo=%s' % (
        r.get('status'), r.get('service'), r.get('volatile'),
        s.get('paused'), s.get('stopped'), s.get('play_origin'), mpc_state, fifo)
    with open(MON + '/state.sig', 'w') as f:
        f.write(sig)

    with open(MON + '/timeline.log', 'a') as f:
        f.write('%s %s STATE router=%s/%s/vol=%s | spotify paused=%s stopped=%s origin=%s pos=%s | mpc=%s | fifo=%s\n' % (
            ts, iso, r.get('status'), r.get('service'), r.get('volatile'),
            s.get('paused'), s.get('stopped'), s.get('play_origin'), t.get('position'),
            mpc, fifo))

    time.sleep(2)
