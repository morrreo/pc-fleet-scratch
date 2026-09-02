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

# ---- two rows on one date that each look fine but cannot both be true -------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9188', 'Total jam': '18',
        'Shift Operator': '2 shift'}),
    row(i='2', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9188', 'HM Akhir': '9204', 'Total jam': '16',
        'Shift Operator': '2 shift'}),
], [fleet_row('SY-75-X1', 'Budi')])
flagged = [r for r in b['records'] if 'impossible_day' in codes(r)]
check('18 h + 16 h on one date flags impossible_day', len(flagged) == 1,
      str([(r['date'], codes(r)) for r in b['records']]))
check('neither 18 h nor 16 h row trips impossible_shift on its own',
      not any('impossible_shift' in codes(r) for r in b['records']))
if flagged:
    print('        msg: ' + [a for a in flagged[0]['anomalies']
                             if a['code'] == 'impossible_day'][0]['msg'])

# ---- one long row is reported once, not twice -------------------------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9180', 'HM Akhir': '9420', 'Total jam': '240'}),
], [fleet_row('SY-75-X1', 'Budi')])
check('a single over-long row is not double-reported as impossible_day',
      codes(b['records'][0]).count('impossible_shift') == 1
      and 'impossible_day' not in codes(b['records'][0]), str(codes(b['records'][0])))

# ---- two normal shifts in one day stay silent -------------------------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9180', 'Total jam': '10',
        'Shift Operator': '1 shift'}),
    row(i='2', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9180', 'HM Akhir': '9190', 'Total jam': '10',
        'Shift Operator': '2 shift'}),
], [fleet_row('SY-75-X1', 'Budi')])
check('a day and a night shift totalling 20 h stay silent',
      not any(r['is_anomaly'] for r in b['records']),
      str([(r['date'], codes(r)) for r in b['records']]))

# ---- same shift label twice in a day, but a believable total ----------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9180', 'Total jam': '10',
        'Shift Operator': '1 shift'}),
    row(i='2', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9180', 'HM Akhir': '9188', 'Total jam': '8',
        'Shift Operator': '1 shift'}),
], [fleet_row('SY-75-X1', 'Budi')])
dupes = [r for r in b['records'] if 'duplicate_entry' in codes(r)]
check('the same shift label filed twice flags duplicate_entry', len(dupes) == 1,
      str([(r['date'], codes(r)) for r in b['records']]))

# ---- unlabelled rows are not duplicates -------------------------------------
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9180', 'Total jam': '10'}),
    row(i='2', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9180', 'HM Akhir': '9190', 'Total jam': '10'}),
], [fleet_row('SY-75-X1', 'Budi')])
check('two rows with no shift label are not called duplicates',
      not any('duplicate_entry' in codes(r) for r in b['records']),
      str([(r['date'], codes(r)) for r in b['records']]))

# ---- reported problems are recorded but never counted as data anomalies -----
b = build([
    row(i='1', Tanggal='8/1/2026', **{'Nama Operator': 'Budi', 'Tipe PC': 'SY 75',
        'Kode PC': 'SY-75-X1', 'HM Awal': '9170', 'HM Akhir': '9180', 'Total jam': '10',
        'Catatan Tambahan / Keterangan': 'Selang hos pecah, tunggu mekanik'}),
], [fleet_row('SY-75-X1', 'Budi')])
r1 = b['records'][0]
check('a breakdown note sets is_problem', r1['is_problem'])
check('and captures the words that matched',
      set(r1['problem_terms']) >= {'pecah', 'selang hos'}, str(r1['problem_terms']))
check('but does NOT set is_anomaly (its hours still bill)',
      not r1['is_anomaly'] and r1['hours'] == 10, str(codes(r1)))

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
