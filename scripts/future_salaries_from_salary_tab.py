#!/usr/bin/env python3
"""Builds salaries_future.csv from the Cyro sheet's "Salary" tab.

The All-Time Database's "Future Salaries" tab (gid 1555460703) holds no data of
its own: it is three IMPORTRANGEs of the "Salary" tab (gid 1402642613) of the
Cyro sheet (14TQPdQ9...), which this repo already downloads as
salaries_2526_current.csv:

    Future Salaries A  <- Salary!A   (PLAYER)
    Future Salaries B  <- Salary!C   (TEAM)
    Future Salaries C:E   empty
    Future Salaries F  <- Salary!K:Z (2027, CH 26-27, ST 26-27, ... TOTAL GUARANTEED)

Reading it through the All-Time Database meant waiting minutes for Google to
open that spreadsheet (Oct 2026); the Cyro sheet answers in seconds. This lays
the Salary tab out the way the Future Salaries export did: same columns, every
row padded to the widest one, trailing empty rows dropped, CRLF line ends, no
line break after the last row.

    python scripts/future_salaries_from_salary_tab.py \\
        data_sources/salaries_2526_current.csv data_sources/salaries_future.csv
"""

import csv
import io
import sys

# Salary tab columns, in Future Salaries order; None is an empty column.
LAYOUT = [0, 2, None, None, None] + list(range(10, 26))   # A, C, -, -, -, K..Z


def future_salaries_csv(salary_tab_text):
    rows = []
    for r in csv.reader(io.StringIO(salary_tab_text)):
        rows.append(['' if i is None or i >= len(r) else r[i] for i in LAYOUT])
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        return ''
    width = max(max((j + 1 for j, c in enumerate(r) if c), default=0) for r in rows)
    out = io.StringIO()
    w = csv.writer(out, lineterminator='\r\n')
    for r in rows:
        w.writerow(r[:width])
    return out.getvalue()[:-2]


def main():
    if len(sys.argv) != 3:
        sys.exit('usage: future_salaries_from_salary_tab.py <salary tab csv> <out csv>')
    with open(sys.argv[1], encoding='utf-8', newline='') as f:
        text = future_salaries_csv(f.read())
    if text.count('\n') < 10:
        sys.exit(f'::error::{sys.argv[1]} gave only {text.count(chr(10)) + 1 if text else 0} rows')
    with open(sys.argv[2], 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    print(f'{sys.argv[2]}: {text.count(chr(10)) + 1} rows from {sys.argv[1]}')


if __name__ == '__main__':
    main()
