#!/usr/bin/env python3
"""Small web server that sends Nokia operator logos and ringtones by SMS through a phone on USB.

GET  /             the tools page (web/index.html) with a "send to a phone" box
GET  /api/status   {"remaining": n, "networks": [...], "password": bool, "country": "49"}
POST /api/send     {"kind": "logo|ringtone", "number": "...", "network": "262 01", "art": "...",
                    "rtttl": "...", "password": "..."}

Settings come from config.ini (see config.example.ini): who may receive SMS (allow/block lists),
daily limits, an optional password and the networks offered for logos. The SMS goes out as a
ready-made PDU with AT+CMGS, so any phone that works as a GSM modem over USB will do.
Numbers are not stored: the per-number limit only keeps a keyed hash that changes every day.
Python standard library only.
"""
import configparser
import fcntl
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import sys
import termios
import threading
import time
import tty
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get('NOKIA_SMS_CONFIG', '/etc/nokia-sms/config.ini')
STATE_DIR = os.environ.get('STATE_DIRECTORY', os.path.join(HERE, 'state'))
PAGE = os.path.join(HERE, '..', 'web', 'index.html')

W, H = 72, 14
# Ringtones: RTTTL in, Nokia Smart Messaging (OTT) out, the same bytes `gammu copyringtone` writes
NOTES = ['c', 'c#', 'd', 'd#', 'e', 'f', 'f#', 'g', 'g#', 'a', 'a#', 'b']
LENGTHS = [1, 2, 4, 8, 16, 32]
BPMS = [25, 28, 31, 35, 40, 45, 50, 56, 63, 70, 80, 90, 100, 112, 125, 140, 160, 180, 200, 225, 250, 285, 320, 355,
        400, 450, 500, 565, 635, 715, 800, 900]
MAX_OTT = 133   # 140 bytes of user data minus the 7 byte port header: one SMS
E164 = re.compile(r'^\+[1-9]\d{6,14}$')

lock = threading.Lock()


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- settings

def split_list(s):
    return [x for x in re.split(r'[\s,;]+', s or '') if x]


def load_config():
    cp = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    cp.optionxform = str   # keep network names as written
    path = CONFIG if os.path.exists(CONFIG) else os.path.join(HERE, 'config.ini')
    if not cp.read(path, encoding='utf-8'):
        log(f'no config at {CONFIG}, using defaults')
    g = lambda sec, key, default='': cp.get(sec, key, fallback=default).strip()
    cfg = {
        'listen': g('server', 'listen', '0.0.0.0:8095'),
        'phone': g('phone', 'device', 'auto'),
        'country': g('numbers', 'home_country', '').lstrip('+'),
        'daily': int(g('limits', 'sms_per_day', '20') or 0),
        'per_number': int(g('limits', 'sms_per_number_per_day', '3') or 0),
        'password': g('access', 'password', ''),
        'networks': [],
    }
    cfg['allow'] = [normalize(x, cfg['country']) or x for x in split_list(g('numbers', 'allow'))]
    cfg['block'] = [normalize(x, cfg['country']) or x for x in split_list(g('numbers', 'block'))]
    if cp.has_section('networks'):
        for name, code in cp.items('networks'):
            if parse_network(code):
                cfg['networks'].append({'name': name, 'code': ' '.join(parse_network(code))})
            else:
                log(f'config: network "{name}" has a broken code "{code}", skipped')
    return cfg


def normalize(raw, country):
    """Any way people write a number -> +<country><number>, or None. Short prefixes are fine (for the lists)."""
    n = re.sub(r'[\s\-/().]', '', str(raw or ''))
    if n.startswith('00'):
        n = '+' + n[2:]
    elif n.startswith('0') and country:
        n = '+' + country + n[1:]
    return n if re.fullmatch(r'\+\d+', n) else None


def check_number(raw, cfg):
    """Returns (number, None) or (None, error text)."""
    n = normalize(raw, cfg['country'])
    if not n or not E164.match(n):
        hint = '' if cfg['country'] else ' Type it with the country code, like +44 7700 900123.'
        return None, 'That doesn\'t look like a phone number.' + hint
    if cfg['allow'] and not any(n.startswith(p) for p in cfg['allow']):
        return None, 'Sorry, this server doesn\'t send to that number.'
    if any(n.startswith(p) for p in cfg['block']):
        return None, 'Sorry, this server doesn\'t send to that number.'
    return n, None


def parse_network(code):
    """'262 01', '262-01', '26201', '310 410' -> ('262', '01') or None."""
    d = re.sub(r'[\s\-/]', '', str(code or ''))
    return (d[:3], d[3:]) if re.fullmatch(r'\d{5,6}', d) else None


# ---------------------------------------------------------------- logo and ringtone

def parse_art(art):
    rows = str(art or '').strip().split('\n')
    if len(rows) != H or any(len(r) != W or set(r) - {'#', '.'} for r in rows):
        return None
    return [[c == '#' for c in r] for r in rows]


def parse_rtttl(text):
    """Returns (name, bpm, [(note 0-11 or None for a rest, octave, length, dotted)]) or an error string."""
    parts = str(text or '').strip().split(':')
    if len(parts) != 3:
        return 'That is not an RTTTL ringtone.'
    name = re.sub(r'[^A-Za-z0-9 _-]', '', parts[0]).strip()[:15].strip() or 'Ringtone'
    d = {'d': 4, 'o': 5, 'b': 63}
    for kv in parts[1].split(','):
        m = re.fullmatch(r'\s*([dob])\s*=\s*(\d{1,3})\s*', kv, re.I)
        if m:
            d[m[1].lower()] = int(m[2])
        elif kv.strip():
            return 'The ringtone settings are broken.'
    if d['d'] not in LENGTHS or not 4 <= d['o'] <= 7 or not 25 <= d['b'] <= 900:
        return 'The ringtone settings are broken.'
    notes = []
    for tok in parts[2].split(','):
        tok = tok.strip().lower()
        if not tok:
            continue
        m = re.fullmatch(r'(\d{1,2})?([a-gp]#?)(\.)?([4-7])?(\.)?', tok)
        if not m or (m[1] and int(m[1]) not in LENGTHS):
            return f'Can\'t read the note "{tok[:10]}". Octaves 4-7 only.'
        notes.append((None if m[2] == 'p' else NOTES.index(m[2]), int(m[4] or d['o']), int(m[1] or d['d']), bool(m[3] or m[5])))
    while notes and notes[0][0] is None:   # gammu drops leading rests too
        notes.pop(0)
    if not notes:
        return 'The ringtone is empty, play some notes first.'
    return name, d['b'], notes


def ringtone_ott(name, bpm, notes):
    """Nokia Smart Messaging ringtone, bit for bit what gammu builds (checked against gammu 1.42)."""
    bits = []

    def add(v, n):
        bits.extend((v >> (n - 1 - i)) & 1 for i in range(n))
    add(0x02, 8); add(0x25, 7); add(0, 1)                    # command length, ringing tone programming, align
    add(0x1D, 7); add(1, 3); add(len(name), 4)               # sound, basic song type, name
    for c in name.encode('ascii'):
        add(c, 8)
    add(1, 8); add(0, 3); add(0, 2); add(5, 4)               # one pattern: header, part A, loop field as gammu writes it
    ins, scale = [], None
    tempo = next((i for i, b in enumerate(BPMS) if b >= bpm), len(BPMS) - 1)   # gammu rounds the tempo up
    for k, (p, octave, length, dot) in enumerate(notes):
        if p is not None and octave - 4 != scale:
            scale = octave - 4
            ins.append((2, scale, 2))                            # scale (octave) change
        if k == 0:
            ins += [(3, 0, 2), (4, tempo, 5)]                    # style natural, tempo
        ins.append((1, ((0 if p is None else p + 1) << 5) | (LENGTHS.index(length) << 2) | int(dot), 9))
    add(len(ins), 8)
    for i, v, n in ins:
        add(i, 3); add(v, n)
    bits += [0] * (-len(bits) % 8)
    add(0, 8)                                                # command end
    return bytes(int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


# ---------------------------------------------------------------- SMS encoding

def bcd(digits):
    digits += 'F' if len(digits) % 2 else ''
    return ''.join(digits[i + 1] + digits[i] for i in range(0, len(digits), 2))


def netcode_bcd(mcc, mnc):
    """MCC/MNC as 3 BCD bytes (3GPP 24.008): 262 02 -> 62 F2 20, 310 410 -> 13 00 14."""
    m3 = mnc[2] if len(mnc) == 3 else 'F'
    return mcc[1] + mcc[0] + m3 + mcc[2] + mnc[1] + mnc[0]


def logo_tpdu(number, mcc, mnc, px):
    """SMS-SUBMIT for a Nokia Smart Messaging operator logo (port 5506), same bytes gammu sends."""
    bitmap = bytearray(W * H // 8)
    for y, row in enumerate(px):
        for x, on in enumerate(row):
            if on:
                i = y * W + x
                bitmap[i // 8] |= 0x80 >> (i % 8)
    udh = '06050415820000'
    data = netcode_bcd(mcc, mnc) + '00' + '480E01' + bitmap.hex().upper()
    return submit(number, udh + data)


def ringtone_tpdu(number, ott):
    """SMS-SUBMIT for a Nokia Smart Messaging ringtone (port 5505), same bytes gammu sends."""
    return submit(number, '06050415810000' + ott.hex().upper())


def submit(number, ud):
    digits = number.lstrip('+')
    # 0x51: SMS-SUBMIT, relative validity, user data header present; PID 00, DCS F5 (8-bit, class 1)
    return f'5100{len(digits):02X}91{bcd(digits)}00F5FF{len(ud) // 2:02X}{ud}'


# ---------------------------------------------------------------- talking to the phone

class Phone:
    """The phone's AT port, opened for one send and closed again, so gammu & co. can use it in between."""

    def __init__(self, device):
        # O_NONBLOCK only for opening (don't wait for a carrier), then normal blocking writes
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, fcntl.fcntl(self.fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        tty.setraw(self.fd)
        attrs = termios.tcgetattr(self.fd)
        attrs[2] |= termios.CLOCAL | termios.CREAD
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def close(self):
        os.close(self.fd)

    def read_until(self, marks, timeout):
        buf, end = b'', time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0 or not select.select([self.fd], [], [], left)[0]:
                return buf
            chunk = os.read(self.fd, 256)
            if not chunk:
                return buf
            buf += chunk
            if any(m in buf for m in marks):
                return buf

    def cmd(self, line, marks=(b'OK', b'ERROR'), timeout=5):
        os.write(self.fd, line.encode() + b'\r')
        return self.read_until(marks, timeout)


def clean(b):
    return ' '.join(b.decode('latin1').split())[:120] or 'no reply'


def find_phone(setting):
    """The configured device, or with 'auto' the first USB serial port that answers AT."""
    if setting and setting != 'auto':
        return setting
    ports = sorted(glob.glob('/dev/serial/by-id/*'), key=lambda p: ('nokia' not in p.lower(), p))
    for port in ports + sorted(glob.glob('/dev/ttyACM*')):
        try:
            ph = Phone(port)
        except OSError:
            continue
        try:
            if b'OK' in ph.cmd('AT', timeout=2):
                log(f'phone found at {port}')
                return port
        finally:
            ph.close()
    return None


def send_pdu(device, tpdu):
    """Returns None on success, otherwise an error text."""
    ph = Phone(device)
    try:
        ph.read_until([], 0.2)                    # drop anything left over
        ph.cmd('ATE0')                            # no echo, so replies are easy to read
        r = ph.cmd('AT+CMGF=0')                   # PDU mode
        if b'OK' not in r:
            return f'AT+CMGF=0: {clean(r)}'
        r = ph.cmd(f'AT+CMGS={len(tpdu) // 2}', (b'>', b'ERROR'), 10)
        if b'>' not in r:
            os.write(ph.fd, b'\x1b')
            return f'AT+CMGS: {clean(r)}'
        os.write(ph.fd, b'00' + tpdu.encode() + b'\x1a')   # 00 = the SIM's own SMS centre
        r = ph.read_until([b'OK', b'ERROR'], 30)
        if b'+CMGS' in r and b'OK' in r:
            return None
        if not r:
            os.write(ph.fd, b'\x1b')
        return f'sending: {clean(r)}'
    finally:
        ph.close()


# ---------------------------------------------------------------- daily limits

def today():
    return datetime.now().strftime('%Y-%m-%d')


def load_state():
    try:
        with open(os.path.join(STATE_DIR, 'state.json')) as f:
            st = json.load(f)
    except (FileNotFoundError, ValueError):
        st = {}
    if st.get('day') != today():
        # new day: new key, so yesterday's number hashes can't be matched to anything any more
        st = {'day': today(), 'sent': 0, 'key': secrets.token_hex(16), 'numbers': {}}
    return st


def save_state(st):
    path = os.path.join(STATE_DIR, 'state.json')
    with open(path + '.tmp', 'w') as f:
        json.dump(st, f)
    os.replace(path + '.tmp', path)


def number_tag(st, number):
    return hmac.new(bytes.fromhex(st['key']), number.encode(), hashlib.sha256).hexdigest()[:16]


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = 'nokia-sms'
    timeout = 15  # drop clients that send their request too slowly, they would hold a thread forever
    cfg = None

    def log_message(self, fmt, *args):
        pass  # no access log: keeps visitors' IPs out of the journal

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            body = open(PAGE, 'rb').read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src blob:; frame-ancestors 'none'")
            self.end_headers()
            return self.wfile.write(body)
        if path != '/api/status':
            return self.reply(404, {'error': 'not found'})
        cfg = self.cfg
        with lock:
            st = load_state()
        self.reply(200, {'remaining': max(0, cfg['daily'] - st['sent']) if cfg['daily'] else None,
                         'networks': cfg['networks'], 'password': bool(cfg['password']), 'country': cfg['country']})

    def do_POST(self):
        if self.path != '/api/send':
            return self.reply(404, {'error': 'not found'})
        # only the page from this server may send: other sites can't post JSON without a CORS
        # preflight, which is never answered here, and browsers mark their requests as cross-site
        if self.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none'):
            return self.reply(403, {'error': 'Not allowed from another website.'})
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.reply(400, {'error': 'Bad request.'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 4096:
                raise ValueError
            req = json.loads(self.rfile.read(length))
            if not isinstance(req, dict):
                raise ValueError
        except (ValueError, OSError):
            return self.reply(400, {'error': 'Bad request.'})
        if req.get('website'):  # honeypot field, invisible to humans
            return self.reply(400, {'error': 'Bad request.'})

        cfg = self.cfg
        if cfg['password'] and not hmac.compare_digest(str(req.get('password', '')).encode(), cfg['password'].encode()):
            time.sleep(1)
            return self.reply(403, {'error': 'Wrong password.'})
        number, err = check_number(req.get('number'), cfg)
        if err:
            return self.reply(400, {'error': err})
        kind = req.get('kind')
        if kind == 'logo':
            net = parse_network(req.get('network'))
            if not net:
                return self.reply(400, {'error': 'Pick the network of the phone that gets the logo.'})
            px = parse_art(req.get('art'))
            if px is None:
                return self.reply(400, {'error': 'The logo must be 72x14 pixels.'})
            if not any(any(r) for r in px):
                return self.reply(400, {'error': 'The logo is empty, paint something first.'})
            tpdu = logo_tpdu(number, *net, px)
        elif kind == 'ringtone':
            tune = parse_rtttl(req.get('rtttl'))
            if isinstance(tune, str):
                return self.reply(400, {'error': tune})
            ott = ringtone_ott(*tune)
            if len(ott) > MAX_OTT:
                return self.reply(400, {'error': f'The ringtone is {len(ott)} bytes, one SMS fits {MAX_OTT}. Delete some notes.'})
            tpdu = ringtone_tpdu(number, ott)
        else:
            return self.reply(400, {'error': 'Bad request.'})

        # one send at a time: the phone sends one SMS at a time anyway, and this keeps the counts exact
        with lock:
            st = load_state()
            left = lambda: max(0, cfg['daily'] - st['sent']) if cfg['daily'] else None
            if cfg['daily'] and st['sent'] >= cfg['daily']:
                return self.reply(429, {'error': 'All SMS for today are used up. Try again tomorrow.', 'remaining': 0})
            tag = number_tag(st, number)
            if cfg['per_number'] and st['numbers'].get(tag, 0) >= cfg['per_number']:
                return self.reply(429, {'error': 'That number got enough SMS for today. Try again tomorrow.', 'remaining': left()})
            device = find_phone(cfg['phone'])
            err = 'no phone found' if not device else None
            if device:
                try:
                    err = send_pdu(device, tpdu)
                except OSError as e:
                    err = str(e)
            if err:
                log(f'send failed: {err}')
                return self.reply(503, {'error': 'The phone didn\'t manage to send it. Try again in a few minutes.', 'remaining': left()})
            st['sent'] += 1
            st['numbers'][tag] = st['numbers'].get(tag, 0) + 1
            save_state(st)
            log(f'{kind} sent ({st["sent"]} today)')
            return self.reply(200, {'ok': True, 'remaining': left()})


def main():
    Handler.cfg = load_config()
    os.makedirs(STATE_DIR, exist_ok=True)
    host, port = Handler.cfg['listen'].rsplit(':', 1)
    srv = ThreadingHTTPServer((host.strip('[]'), int(port)), Handler)
    log(f'open http://{"localhost" if host in ("0.0.0.0", "::", "[::]") else host}:{port} in a browser')
    srv.serve_forever()


if __name__ == '__main__':
    main()
