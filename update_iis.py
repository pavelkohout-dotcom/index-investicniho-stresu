"""Update the standalone IIS page using dated observations."""
import bisect
import csv
import io
import json
import math
import re
import subprocess
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

PAGE = Path(__file__).with_name('index.html')
ARCHIVE = 'https://dailymarket.report/archive/'
DAY = timedelta(days=1)


def fetch(url, optional=False):
    result = subprocess.run(['curl', '-LsS', '--retry', '3', '--max-time', '45',
                             '-w', '\n%{http_code}', url], capture_output=True, check=True)
    body, status = result.stdout.rsplit(b'\n', 1)
    if optional and status == b'404':
        return None
    if status != b'200':
        raise RuntimeError(f'{url}: HTTP {status.decode()}')
    return body.decode('utf-8-sig')


def cell(value, source):
    return [f'{value:.2f}', source, str(value), '']


def estimate(truflation):
    return truflation * D('.62883') + D('1.406815')


def index(cpi, vix, change):
    return (cpi * D('1.9') + vix * D('.35') + change) / D('3.1')


def cz(d):
    return f'{d.day}. {d.month}. {d.year}'


def chart(points, end):
    first = points[0][0]
    low = math.floor(min(v for _, v in points))
    high = math.ceil(max(v for _, v in points))
    def x(d):
        return 40 + 850 * (d-first).days / (end-first).days
    def y(v):
        return 32 + 344 * (high-float(v)) / max(1, high-low)
    svg = ['<svg viewBox="0 0 920 452" style="width:100%;min-width:540px" role="img" aria-label="Vývoj indexu IIS">']
    for v in range(low, high+1, max(1, math.ceil((high-low)/5))):
        svg.append(f'<path d="M40 {y(v):.2f}h850"/><text x="28" y="{y(v)+5:.2f}" text-anchor="end">{v}</text>')
    ticks = [(first, first.strftime('%m/%Y'))]
    for year in range(first.year+1, end.year+1):
        d = date(year, 1, 1)
        if x(d)-x(ticks[-1][0]) > 55 and x(end)-x(d) > 55:
            ticks.append((d,str(year)))
    for d,label in ticks+[(end,end.strftime('%m/%Y'))]:
        svg.append(f'<text x="{x(d):.2f}" y="436" text-anchor="middle">{label}</text>')
    coords = ' '.join(f'{x(d):.2f},{y(v):.2f}' for d,v in points)
    svg.append(f'<polyline fill="none" stroke-width="2" points="{coords}"/>')
    for d,v in points:
        svg.append(f'<circle cx="{x(d):.2f}" cy="{y(v):.2f}" r="2"><title>{d:%d.%m.%Y} · IIS {v:.2f}</title></circle>')
    return ''.join(svg)+'</svg>'


def update():
    today = datetime.now(ZoneInfo('Europe/Prague')).date()
    head, rest = PAGE.read_text().split('const sheets=', 1)
    raw, tail = rest.split(';const letter', 1)
    sheets = json.loads(raw)
    rows = sheets[1]
    dates = {date.fromisoformat(row['1'][2]): row for n,row in rows.items() if int(n)>=1159}
    start = max(min(dates), max(d for d,r in dates.items() if '8' in r)-7*DAY)
    vix = {datetime.strptime(r['DATE'],'%m/%d/%Y').date(): D(r['CLOSE'])
           for r in csv.DictReader(io.StringIO(fetch('https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv')))}
    yields = {date.fromisoformat(r['observation_date']):D(r['DGS10']) for r in csv.DictReader(io.StringIO(
        fetch(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd={start-380*DAY}'))) if r['DGS10']}
    if min(yields)>start-365*DAY or not vix:
        raise ValueError('Incomplete market data')
    yd = sorted(yields)
    rates = {}
    d = start
    while d <= today:
        url = ARCHIVE + d.isoformat()+'.html'
        report = fetch(url, optional=True)
        if report:
            stamp = re.search(r'Truflation data as of (\d{4}-\d\d-\d\d)',report)
            value = re.search(r'class="infl-big">\s*(-?[\d.]+)%',report)
            if not stamp or not value:
                raise ValueError(f'Unrecognized inflation report: {url}')
            observed = date.fromisoformat(stamp[1])
            if observed > d:
                raise ValueError(f'Future observation: {url}')
            rates[observed] = (D(value[1]),url)
        d += DAY
    if not rates:
        raise ValueError('No dated inflation readings retrieved')
    d = start
    while d <= today:
        r = dates.setdefault(d, {'1':[d.strftime('%d/%m/%y'),'',str(d),'']})
        if d in rates:
            tr,url = rates[d]
            r['2'] = cell(tr,url)
            r['3'] = cell(estimate(tr),'B × 0.62883 + 1.406815')
        if d in vix:
            r['4'] = cell(vix[d],'Cboe · závěrečná hodnota')
        if d in yields:
            prior = d.replace(year=d.year-1, day=28 if (d.month,d.day)==(2,29) else d.day)
            prior = yd[bisect.bisect_right(yd,prior)-1]
            r['6'] = cell(yields[d]-yields[prior],f'{d}: {yields[d]} − {prior}: {yields[prior]}')
        if all(k in r for k in ('3','4','6')):
            r['8'] = cell(index(*(D(r[k][2]) for k in ('3','4','6'))),'(1.9 × C + 0.35 × D + F) / 3.1')
        d += DAY
    for n,d in enumerate(sorted(dates),1159):
        rows[str(n)] = dates[d]
    points = sorted((date.fromisoformat(r['1'][2]),D(r['8'][2])) for n,r in rows.items() if int(n)>=947 and '8' in r)
    latest,value = points[-1]
    value = f'{value:.2f}'.replace('.',',')
    for label,text in [('Poslední hodnota IIS',value),('Datum hodnoty',cz(latest)),('Aktualizováno',cz(today))]:
        head,count = re.subn(f'(<dt>{label}</dt><dd>).*?(</dd>)',lambda m:m[1]+text+m[2],head)
        assert count==1
    head,count = re.subn('<svg .*?</svg>',lambda _:chart(points,today),head,count=1,flags=re.S)
    assert count==1
    head = re.sub(r'Poslední hodnota: [^<]+',f'Poslední hodnota: {value} ({cz(latest)}).',head)
    head = re.sub(r'(<summary>Technické poznámky</summary><p>).*?</p>',r'\1Pomlčka označuje chybějící hodnotu. Každý údaj odpovídá datu měření.</p>',head)
    out = head+'const sheets='+json.dumps(sheets).replace('<',r'\u003c')+';const letter'+tail
    temp = PAGE.with_suffix('.tmp')
    temp.write_text(out)
    temp.replace(PAGE)
    print(f'{len(points)} observations; last complete IIS: {latest} = {value}')


if __name__ == '__main__':
    assert round(index(estimate(D('3.22')),D('19.30'),D('1.04')),2)==D('4.62')
    update()
