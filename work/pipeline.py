#!/usr/bin/env python3
"""
PC Fleet Dashboard — build pipeline.
Usage: python3 pipeline.py <drive_result.json|sheet.txt> [shell.html] [out.html]
Source: Google Sheet "LAPORAN PC", file id 1dQKf8v7CT2wo1K9DQXAU_GJBNP6xWkYnFCkyRPQvAXE, tab "Laporan PC Form".
"""
import json, re, sys, os, datetime
from collections import defaultdict

RATE_BY_TIPE = {'75': 450000, '200': 800000}
WAGE_PER_HOUR = 40000

# Shape of the data this script hands the page. Bump it whenever the bundle's
# structure changes, and bump BUNDLE_SCHEMA in the page to match. The page checks
# the two agree and says so loudly if they don't — an older pipeline splicing into
# a newer page used to fail silently, publishing blank alert cards.
SCHEMA = 2

# A shift cannot outlast a calendar day. The longest real shift in the log so far
# is 22 h, so 24 leaves headroom without swallowing a genuine 240 h typo.
MAX_SHIFT_HOURS = 24
# Consecutive shifts on a healthy unit close and open on the exact same reading
# (83 of 91 transitions do). Anything above this is a real gap, not rounding.
HM_GAP_SLACK = 0
# Below this a forward gap is a stray hour rather than a whole missing shift.
HM_GAP_ALERT = 2


def fmt_h(v):
    """Render an hour count without a pointless trailing .0."""
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return '{:,}'.format(v)

PROBLEM_WORDS = ['rusak', 'pecah', 'putus', 'bocor', 'trouble', 'ambles', 'amblas', 'macet',
                 'patah', 'error', 'gagal', 'mogok', 'tunggu', 'nyalep', 'selang hos', 'vanbelt',
                 'terjebak', 'anjlok', 'terguling', 'tergelincir', 'hilang', 'hancur',
                 'kebakaran', 'kebocoran']

# Deliberately NARROW. "servis tapak timbun" is routine site grading, NOT a machine
# service, and must not match. Do not loosen these.
SERVICE_PATTERNS = [
    r'servis\s*/\s*\d+\s*jam', r'servis\s+rutin', r'servis\s+mekanik', r'ganti\s+oli',
    r'ganti\s+filter', r'servis\s+baket', r'servis\s+hm\s*\d+',
    r'\bhm\s*\d{4,5}\b.*servis', r'servis.*\bhm\s*\d{4,5}\b',
]
SERVICE_RE = [re.compile(p, re.I) for p in SERVICE_PATTERNS]

COLS_A = ['i', 'Tanggal', 'Nama Operator', 'Email Address', 'Merek PC', 'Tipe PC', 'Kode PC',
          'Tipe Bahan Bakar', 'Bahan Bakar yang di pakai', 'Unit Solar', 'Gas', 'Jenis Pekerjaan',
          'Catatan Tambahan / Keterangan', 'Lokasi', 'Shift Operator', 'HM Awal', 'HM Akhir',
          'Foto HM', 'Total jam', 'blank', 'TAGIHAN', 'GAJIAN']
COLS_B = ['MEREK PC', 'TIPE PC', 'SPEK PC', 'KODE PC', 'NOMOR SERIAL PC',
          'Operator AWAL', 'Operator BERJALAN']


def load_text(path):
    raw = open(path, encoding='utf-8').read()
    stripped = raw.lstrip()
    if stripped.startswith('{'):
        return json.loads(raw)['fileContent']
    return raw


def cells(line):
    s = line.strip()
    if not s.startswith('|'):
        return None
    parts = s.split('|')[1:-1]
    return [p.strip().replace('\\-', '-').replace('\\_', '_').replace('\\*', '*') for p in parts]


def num(s):
    if s is None:
        return None
    s = s.strip().replace(',', '')
    if s in ('', '-'):
        return None
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except ValueError:
        return None


def job_category(text):
    t = text.lower()
    if 'rental' in t:
        return 'Rental HM'
    for k in ('loading', 'loding', 'kupas tanah', 'brasing'):
        if k in t:
            return 'Loading & Hauling'
    for k in ('cuci parit', 'tanggul', 'gorong', 'bandar', 'dam air'):
        if k in t:
            return 'Drainage & Water Control'
    for k in ('jalan panen', 'jln panen', 'tapak timbun', 'jalur panen'):
        if k in t:
            return 'Road / Path Maintenance'
    for k in ('ampar', 'finishing', 'puru'):
        if k in t:
            return 'Puru Spreading & Finishing'
    if 'land clearing' in t or t.strip() == 'lc':
        return 'Land Clearing'
    return 'Other'


def parse(text):
    lines = text.split('\n')
    ia = ib = None
    for i, l in enumerate(lines):
        if ia is None and 'Nama Operator' in l and 'Kode PC' in l:
            ia = i
        if ib is None and 'Operator AWAL' in l:
            ib = i
    if ia is None:
        raise SystemExit('PARSE_FAIL: Table A header ("Nama Operator" + "Kode PC") not found — '
                         'the sheet columns may have changed.')
    if ib is None:
        raise SystemExit('PARSE_FAIL: Table B header ("Operator AWAL") not found.')
    if len(cells(lines[ia])) != len(COLS_A):
        raise SystemExit('PARSE_FAIL: Table A has %d columns, expected %d — columns changed.'
                         % (len(cells(lines[ia])), len(COLS_A)))
    if len(cells(lines[ib])) != len(COLS_B):
        raise SystemExit('PARSE_FAIL: Table B has %d columns, expected %d — columns changed.'
                         % (len(cells(lines[ib])), len(COLS_B)))

    raw_a = []
    for l in lines[ia + 1:]:
        c = cells(l)
        if c is None or len(c) != len(COLS_A):
            break
        raw_a.append(dict(zip(COLS_A, c)))

    fleet = []
    for l in lines[ib + 1:]:
        c = cells(l)
        if c is None or len(c) != len(COLS_B):
            break
        row = dict(zip(COLS_B, c))
        if row['KODE PC']:
            fleet.append(row)
    return raw_a, fleet


def build(raw_a, fleet):
    by_kode = {f['KODE PC']: f for f in fleet}
    by_running = {f['Operator BERJALAN'].strip().lower(): f['KODE PC']
                  for f in fleet if f['Operator BERJALAN'].strip()}

    records = []
    for r in raw_a:
        tgl = r['Tanggal'].strip()
        m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', tgl)
        if not m:
            continue
        mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        date = '%04d-%02d-%02d' % (yr, mo, da)

        kode = r['Kode PC'].split(' (')[0].strip()
        operator = r['Nama Operator'].strip()
        if not kode:
            kode = by_running.get(operator.lower(), '')

        hm_start, hm_end = num(r['HM Awal']), num(r['HM Akhir'])
        hours = num(r['Total jam'])
        if hours is None and hm_start is not None and hm_end is not None:
            hours = hm_end - hm_start
        if hours is None:
            hours = 0

        tipe = r['Tipe PC'].strip()
        rate = next((v for k, v in RATE_BY_TIPE.items() if k in tipe), 0)

        job = r['Jenis Pekerjaan'].strip()
        notes = r['Catatan Tambahan / Keterangan'].strip()
        blob = (job + ' ' + notes).lower()
        unit = by_kode.get(kode, {})
        # Keep the words that matched, so an alert can say what was reported
        # rather than just that something was.
        problem_terms = sorted({w for w in PROBLEM_WORDS if w in blob})

        records.append({
            'submitted': r['i'].strip(), 'date': date, 'operator': operator,
            'email': r['Email Address'].strip(),
            'merek': r['Merek PC'].strip() or unit.get('MEREK PC', ''),
            'tipe_pc': tipe, 'kode_pc': kode,
            'spek': unit.get('SPEK PC', ''), 'serial': unit.get('NOMOR SERIAL PC', ''),
            'fuel_type': r['Tipe Bahan Bakar'].strip(),
            'fuel_qty': num(r['Bahan Bakar yang di pakai']),
            'fuel_unit': r['Unit Solar'].strip(), 'gas': num(r['Gas']),
            'job': job, 'notes': notes, 'job_category': job_category(job),
            'lokasi': r['Lokasi'].strip(), 'shift': r['Shift Operator'].strip(),
            'hm_start': hm_start, 'hm_end': hm_end, 'hours': hours,
            'rate_per_hour': rate, 'billing_idr': hours * rate,
            'wage_idr': hours * WAGE_PER_HOUR,
            'is_problem': bool(problem_terms), 'problem_terms': problem_terms,
            'is_service': any(p.search(blob) for p in SERVICE_RE),
            'anomalies': [], 'is_anomaly': False,
        })

    byunit = defaultdict(list)
    for rec in records:
        byunit[rec['kode_pc']].append(rec)

    for kode, recs in byunit.items():
        reg_name = by_kode.get(kode, {}).get('Operator BERJALAN', '').strip()
        registered = reg_name.lower()

        # Walk the log in DATE order. Sorting by hm_start instead lets a mistyped
        # reading sort into the wrong place, which flags its innocent neighbours
        # and hides the entry that is actually wrong.
        prev = None
        for rec in sorted([x for x in recs if x['hm_start'] is not None],
                          key=lambda x: x['date']):
            if prev is not None:
                gap = rec['hm_start'] - prev['hm_end']
                if gap > HM_GAP_SLACK:
                    # The meter ran while nobody filed a report for it.
                    rec['anomalies'].append({
                        'code': 'missing_report',
                        'severity': 'alert' if gap > HM_GAP_ALERT else 'info',
                        'hours': gap,
                        'ref_date': prev['date'],
                        'title': '%s h missing between shifts' % fmt_h(gap),
                        # {d} is filled in by the page so dates format one way only.
                        'msg': '{d} closed at HM %s, but this shift opens at HM %s. '
                               'Those %s h ran with no report filed.'
                               % (fmt_h(prev['hm_end']), fmt_h(rec['hm_start']), fmt_h(gap)),
                    })
                elif gap < -HM_GAP_SLACK:
                    # An hour meter only counts up, so this is a typo or wrong unit.
                    rec['anomalies'].append({
                        'code': 'hm_backwards',
                        'severity': 'alert',
                        'ref_date': prev['date'],
                        'title': 'Meter went backwards %s h' % fmt_h(-gap),
                        'msg': 'This shift opens at HM %s, below the HM %s that {d} '
                               'closed at. An hour meter cannot count down — check for a '
                               'mistyped digit or the wrong unit on the form.'
                               % (fmt_h(rec['hm_start']), fmt_h(prev['hm_end'])),
                    })
            if rec['hm_end'] is not None:
                prev = rec

        for rec in recs:
            span = None
            if rec['hm_start'] is not None and rec['hm_end'] is not None:
                span = rec['hm_end'] - rec['hm_start']
            longest = max(rec['hours'], span) if span is not None else rec['hours']

            if longest > MAX_SHIFT_HOURS:
                rec['anomalies'].append({
                    'code': 'impossible_shift',
                    'severity': 'alert',
                    'title': '%s h in a single shift' % fmt_h(longest),
                    'msg': '%s h is longer than a calendar day. An excavator cannot run '
                           'this long unbroken — the start or end reading is almost '
                           'certainly mistyped.' % fmt_h(longest),
                })
            if span is not None and span < 0:
                rec['anomalies'].append({
                    'code': 'negative_shift',
                    'severity': 'alert',
                    'title': 'Shift ends below its own start',
                    'msg': 'Closes at HM %s but opened at HM %s.'
                           % (fmt_h(rec['hm_end']), fmt_h(rec['hm_start'])),
                })
            elif rec['hours'] < 0:
                rec['anomalies'].append({
                    'code': 'negative_shift',
                    'severity': 'alert',
                    'title': 'Negative hours reported',
                    'msg': 'Reported total is %s h.' % fmt_h(rec['hours']),
                })
            if span is not None and abs(span - rec['hours']) > 0.5:
                rec['anomalies'].append({
                    'code': 'hours_mismatch',
                    'severity': 'alert',
                    'title': 'Reported hours disagree with the meter',
                    'msg': 'Reported %s h, but the meter moved %s h (HM %s → %s).'
                           % (fmt_h(rec['hours']), fmt_h(span),
                              fmt_h(rec['hm_start']), fmt_h(rec['hm_end'])),
                })
            # A shift logged against a unit by someone other than its running
            # operator is usually the wrong unit picked on the form.
            if registered and rec['operator'].strip().lower() != registered:
                rec['anomalies'].append({
                    'code': 'operator_mismatch',
                    'severity': 'alert',
                    'title': 'Logged by a different operator',
                    'msg': 'Filed by %s, but %s is the running operator for this unit. '
                           'The wrong unit may have been picked on the form.'
                           % (rec['operator'] or '—', reg_name or '—'),
                })
            rec['is_anomaly'] = bool(rec['anomalies'])

        # A machine cannot run more than a calendar day inside a calendar day.
        # Rows that each look reasonable alone can still add up to an impossible
        # day, so this is checked on the day total rather than per row.
        per_day = defaultdict(list)
        for rec in recs:
            per_day[rec['date']].append(rec)
        for date, drecs in per_day.items():
            total = sum(x['hours'] for x in drecs)
            impossible = total > MAX_SHIFT_HOURS and len(drecs) > 1
            if not impossible:
                # Same machine, same date, same shift label, filed more than once.
                # (When the day is already impossible, that says it better.)
                by_shift = defaultdict(list)
                for x in drecs:
                    label = x['shift'].strip().lower()
                    if label:  # two unlabelled rows are not evidence of a duplicate
                        by_shift[label].append(x)
                for shift, srecs in by_shift.items():
                    if len(srecs) < 2:
                        continue
                    target = sorted(srecs, key=lambda x: x['hm_start']
                                    if x['hm_start'] is not None else 0)[-1]
                    target['anomalies'].append({
                        'code': 'duplicate_entry',
                        'severity': 'alert',
                        'title': 'Same shift filed %d times' % len(srecs),
                        'msg': '%d rows share this machine, date and shift label (%s). '
                               'One is probably a duplicate submission.'
                               % (len(srecs), srecs[0]['shift'] or 'unlabelled'),
                    })
                    target['is_anomaly'] = True
            if impossible:
                # Flag once, on the last row of the day, so one problem is one alert.
                target = sorted(drecs, key=lambda x: x['hm_start'] if x['hm_start'] is not None else 0)[-1]
                shifts = ', '.join(sorted(set(x['shift'] for x in drecs if x['shift']))) or 'unlabelled'
                target['anomalies'].append({
                    'code': 'impossible_day',
                    'severity': 'alert',
                    'title': '%s h logged in a single day' % fmt_h(total),
                    'msg': '%d shift rows on this date total %s h for one machine, more '
                           'than the day holds (shift labels: %s). Check whether a row is '
                           'duplicated or belongs to another date.'
                           % (len(drecs), fmt_h(total), shifts),
                })
                target['is_anomaly'] = True

    units = {}
    for kode, recs in byunit.items():
        if not kode:
            continue
        # The hour meter is whatever the most recent shift closed at. Using max()
        # instead lets one mistyped high reading inflate the unit's HM permanently.
        dated = sorted([r_ for r_ in recs if r_['hm_end'] is not None],
                       key=lambda r_: r_['date'])
        if dated:
            current_hm = dated[-1]['hm_end']
        else:
            hms = [x for r_ in recs for x in (r_['hm_start'], r_['hm_end']) if x is not None]
            current_hm = max(hms) if hms else 0
        svc = [r_ for r_ in recs if r_['is_service']]
        svc_hms = [x for r_ in svc for x in (r_['hm_start'], r_['hm_end']) if x is not None]
        if svc_hms:
            last_service_hm, known = max(svc_hms), True
        else:
            last_service_hm, known = current_hm, False
        ok = [r_ for r_ in recs if not r_['is_anomaly']]
        f = by_kode.get(kode, {})
        units[kode] = {
            'kode_pc': kode, 'merek': f.get('MEREK PC', ''), 'tipe': f.get('TIPE PC', ''),
            'spek': f.get('SPEK PC', ''), 'serial': f.get('NOMOR SERIAL PC', ''),
            'operator_origin': f.get('Operator AWAL', ''),
            'operator_current': f.get('Operator BERJALAN', ''),
            'current_hm': current_hm, 'last_service_hm': last_service_hm,
            'service_hm_known': known, 'hours_since_service': current_hm - last_service_hm,
            'total_hours_logged': sum(r_['hours'] for r_ in ok),
            'billing_total_idr': sum(r_['billing_idr'] for r_ in ok),
            'n_records': len(recs),
            'problem_count': sum(1 for r_ in recs if r_['is_problem']),
            'anomaly_count': sum(1 for r_ in recs if r_['is_anomaly']),
        }
    for kode, f in by_kode.items():
        if kode not in units:
            units[kode] = {
                'kode_pc': kode, 'merek': f['MEREK PC'], 'tipe': f['TIPE PC'],
                'spek': f['SPEK PC'], 'serial': f['NOMOR SERIAL PC'],
                'operator_origin': f['Operator AWAL'], 'operator_current': f['Operator BERJALAN'],
                'current_hm': 0, 'last_service_hm': 0, 'service_hm_known': False,
                'hours_since_service': 0, 'total_hours_logged': 0, 'billing_total_idr': 0,
                'n_records': 0, 'problem_count': 0, 'anomaly_count': 0}

    ops = {}
    for rec in records:
        o = rec['operator']
        if not o:
            continue
        d = ops.setdefault(o, {'operator': o, 'total_hours': 0, 'wage_total_idr': 0,
                               'units_used': [], 'n_records': 0, 'problem_count': 0,
                               'last_date': ''})
        d['n_records'] += 1
        if not rec['is_anomaly']:
            d['total_hours'] += rec['hours']
            d['wage_total_idr'] += rec['wage_idr']
        if rec['kode_pc'] and rec['kode_pc'] not in d['units_used']:
            d['units_used'].append(rec['kode_pc'])
        if rec['is_problem']:
            d['problem_count'] += 1
        if rec['date'] > d['last_date']:
            d['last_date'] = rec['date']

    daily = {}
    for rec in records:
        daily.setdefault(rec['date'], {})
        daily[rec['date']][rec['kode_pc']] = daily[rec['date']].get(rec['kode_pc'], 0) + rec['hours']

    dates = sorted(r_['date'] for r_ in records)
    return {
        'records': records, 'units': units, 'operators': ops, 'daily': daily,
        'generated_at': datetime.date.today().isoformat(),
        'meta': {
            'schema': SCHEMA,
            'data_source': 'LAPORAN PC (Google Sheet) — "Laporan PC Form" tab',
            'date_min': dates[0] if dates else None,
            'date_max': dates[-1] if dates else None,
        },
    }


def splice(shell_path, bundle, out_path):
    """Replace ONLY the `const BUNDLE = ...;` object. Every other byte is preserved."""
    html = open(shell_path, encoding='utf-8').read()
    before = len(html)
    payload = 'const BUNDLE = ' + json.dumps(bundle, ensure_ascii=False) + ';'

    m = re.search(r'const\s+BUNDLE\s*=\s*', html)
    if not m:
        raise SystemExit('SPLICE_FAIL: no `const BUNDLE =` found in the shell.')
    i = html.index('{', m.end())
    depth, j, instr, esc = 0, i, None, False
    while j < len(html):
        ch = html[j]
        if instr:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == instr:
                instr = None
        elif ch in '"\'':
            instr = ch
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                break
        j += 1
    else:
        raise SystemExit('SPLICE_FAIL: unbalanced BUNDLE object.')
    k = j + 1
    while k < len(html) and html[k] in ' \t\r\n':
        k += 1
    if k < len(html) and html[k] == ';':
        k += 1
    start, end = m.start(), k
    out = html[:start] + payload + html[end:]

    chrome_in = html[:start] + html[end:]
    chrome_out = out[:start] + out[start + len(payload):]
    if chrome_in != chrome_out:
        raise SystemExit('SPLICE_FAIL: page chrome changed — the BUNDLE match was wrong.')
    if '<title' not in out.lower():
        raise SystemExit('SPLICE_FAIL: result has no <title> tag.')
    if json.loads(out[start + len('const BUNDLE = '):start + len(payload) - 1]) is None:
        raise SystemExit('SPLICE_FAIL: spliced BUNDLE does not re-parse.')

    open(out_path, 'w', encoding='utf-8').write(out)
    return before, len(out)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    text = load_text(sys.argv[1])
    raw_a, fleet = parse(text)
    bundle = build(raw_a, fleet)

    with open('bundle.json', 'w', encoding='utf-8') as fh:
        json.dump(bundle, fh, ensure_ascii=False)

    m = bundle['meta']
    print('OK  records=%d  range=%s..%s  units=%d  operators=%d'
          % (len(bundle['records']), m['date_min'], m['date_max'],
             len(bundle['units']), len(bundle['operators'])))
    for k, u in sorted(bundle['units'].items()):
        print('    %-12s n=%-3d hm=%-6s problems=%-2d anomalies=%-2d service_known=%s'
              % (k, u['n_records'], u['current_hm'], u['problem_count'],
                 u['anomaly_count'], u['service_hm_known']))

    if len(sys.argv) >= 3:
        out_path = sys.argv[3] if len(sys.argv) >= 4 else 'dashboard.html'
        before, after = splice(sys.argv[2], bundle, out_path)
        print('OK  spliced %s -> %s (%d -> %d bytes)'
              % (os.path.basename(sys.argv[2]), out_path, before, after))


if __name__ == '__main__':
    main()
