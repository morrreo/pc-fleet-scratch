#!/usr/bin/env python3
"""Synthetic checks for the HM alert rules, using the exact cases raised:

  1. a shift closing at HM 9180 followed by one opening at 9210 -> 30 missing hours
  2. a shift running 9180 -> 9420 -> 240 h, impossible in one shift

Neither case exists in the live sheet yet, so without this they would ship
untested. Run: python3 test_alerts.py
"""
import pipeline

COLS_A, COLS_B = pipeline.COLS_A, pipeline.COLS_B


def row(**kw):
    r = {c: '' for c in COLS_A}
    r.update(kw)
    return '| ' + ' | '.join(r[c] for c in COLS_A) + ' |'


def fleet_row(kode, operator):
    r = dict(zip(COLS_B, ['SANY', 'SY 75', 'standard', kode, 'SN-1', operator, operator]))
    return '| ' + ' | '.join(r[c] for c in COLS_B) + ' |'


def sheet(rows, fleet):
    return '\n'.join(
        ['| ' + ' | '.join(COLS_A) + ' |'] + rows + [''] +
        ['| ' + ' | '.join(COLS_B) + ' |'] + fleet)


def codes(rec):
    return sorted(a['code'] for a in rec['anomalies'])


def build(rows, fleet):
    raw_a, fl = pipeline.parse(sheet(rows, fleet))
    return pipeline.build(raw_a, fl)


FAIL = []


def check(name, cond, detail=''):
    print(('  PASS  ' if cond else '  FAIL  ') + name + (('  -> ' + detail) if detail and not cond else ''))
    if not cond:
        FAIL.append(name)


# ---- case 1: gap between shifts (the "missing report" case) ----------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9180', 'Total jam': '10'}),
    row(i='2', Tanggal='8/2/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9210', 'HM Akhir': '9220', 'Total jam': '10'}),
], [fleet_row('SY-75-X1', 'Budi')])
r2 = [r for r in b['records'] if r['date'] == '2026-08-02'][0]
a = [x for x in r2['anomalies'] if x['code'] == 'missing_report']
check('gap 9180->9210 flags missing_report', bool(a), str(codes(r2)))
check('gap reports 30 h', bool(a) and a[0]['hours'] == 30, str(a[0]['hours']) if a else 'n/a')
check('gap severity is alert', bool(a) and a[0]['severity'] == 'alert')
if a:
    print('        msg: ' + a[0]['msg'])

# ---- case 2: impossible single shift ---------------------------------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9180', 'HM Akhir': '9420', 'Total jam': '240'}),
], [fleet_row('SY-75-X1', 'Budi')])
r1 = b['records'][0]
a = [x for x in r1['anomalies'] if x['code'] == 'impossible_shift']
check('9180->9420 flags impossible_shift', bool(a), str(codes(r1)))
check('impossible shift severity is alert', bool(a) and a[0]['severity'] == 'alert')
if a:
    print('        msg: ' + a[0]['msg'])

# ---- case 2b: caught even when "Total jam" is quietly plausible -------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9180', 'HM Akhir': '9420', 'Total jam': '12'}),
], [fleet_row('SY-75-X1', 'Budi')])
r1 = b['records'][0]
check('impossible span caught despite a plausible Total jam',
      'impossible_shift' in codes(r1), str(codes(r1)))
check('and the reported/meter disagreement is flagged too',
      'hours_mismatch' in codes(r1), str(codes(r1)))

# ---- meter running backwards ------------------------------------------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9180', 'Total jam': '10'}),
    row(i='2', Tanggal='8/2/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9080', 'HM Akhir': '9090', 'Total jam': '10'}),
], [fleet_row('SY-75-X1', 'Budi')])
r2 = [r for r in b['records'] if r['date'] == '2026-08-02'][0]
check('meter going backwards flags hm_backwards', 'hm_backwards' in codes(r2), str(codes(r2)))

# ---- a clean log must stay silent (no false alarms) -------------------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9180', 'Total jam': '10'}),
    row(i='2', Tanggal='8/2/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9180', 'HM Akhir': '9192', 'Total jam': '12'}),
    row(i='3', Tanggal='8/3/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9192', 'HM Akhir': '9214', 'Total jam': '22'}),
], [fleet_row('SY-75-X1', 'Budi')])
noisy = [(r['date'], codes(r)) for r in b['records'] if r['is_anomaly']]
check('a continuous log raises nothing (22 h shift is legal)', not noisy, str(noisy))

print('\n' + ('ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)))
raise SystemExit(1 if FAIL else 0)
