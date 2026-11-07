#!/usr/bin/env python3
"""按「最近完整周六→周五」口径生成群周报长图 HTML（浅色卡片风格）。

本仓库只放代码。含群聊原文的周报数据一律放在外部数据目录
~/wechat-export/ 下，不进仓库、不提交；生成的 HTML/PNG 也默认写到
~/wechat-export/cards/。

用法：
    python3 weekly_cards.py                                  # 读默认外部数据文件
    python3 weekly_cards.py <data.json> [输出目录]           # 自定义路径

数据文件为 JSON，顶层字段：
    period / period_note / date_filter / quote_range / made_at / week_no
    file_prefix（输出文件名前缀）、groups（每个群的卡片数据数组）
每个 group 含：key、kicker、accent、soft、title、h1、lede、stats、bars、
    chart_cap、topics、quotes、foot_stat、foot_note，可选 table、notice。

生成 HTML 后，用 shot_long.js 转成长图：
    node shot_long.js <input.html> <output.png> 1080 2
"""
import html
import json
import os
import sys
from pathlib import Path

# 数据默认位置在仓库之外；可用环境变量或命令行参数覆盖
DATA_HOME = Path(os.environ.get('WECHAT_EXPORT_HOME', Path.home() / 'wechat-export'))
DEFAULT_DATA = Path(os.environ.get('WEEKLY_DATA', DATA_HOME / 'weekly_data.json'))

CSS = """
  *{margin:0;padding:0;box-sizing:border-box;}
  :root{
    --paper:#F8F6F1; --card:#FFFFFF; --ink:#17181A; --ink2:#4E5257; --ink3:#8B9096;
    --line:#E8E5DE; --line2:#F0EEE9; --accent:__ACCENT__; --accent-soft:__SOFT__;
  }
  html,body{background:var(--paper);}
  body{width:1080px;font-family:"PingFang SC","Hiragino Sans GB","Noto Sans SC","Microsoft YaHei",sans-serif;
    color:var(--ink);-webkit-font-smoothing:antialiased;}
  .wrap{padding:0 72px 0;}
  header{padding:84px 0 0;}
  .kicker{display:flex;align-items:center;gap:16px;}
  .kicker span{font-size:19px;letter-spacing:.26em;color:var(--accent);font-weight:600;}
  .kicker i{display:block;width:52px;height:1.5px;background:var(--accent);}
  h1{margin:30px 0 0;font-size:60px;line-height:1.28;font-weight:800;letter-spacing:-.015em;}
  .period{margin-top:26px;display:flex;align-items:baseline;gap:20px;font-size:22px;color:var(--ink2);}
  .period b{font-size:34px;font-weight:750;color:var(--ink);}
  .period span{color:var(--ink3);font-size:19px;}
  .lede{margin-top:36px;padding-top:34px;border-top:1px solid var(--line);
    font-size:25px;line-height:1.92;color:var(--ink2);max-width:920px;}
  .lede b{color:var(--ink);font-weight:650;}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);margin-top:44px;
    border-top:1px solid var(--line);border-bottom:1px solid var(--line);}
  .stat{padding:34px 0 32px;position:relative;}
  .stat+.stat{padding-left:38px;}
  .stat+.stat::before{content:"";position:absolute;left:0;top:34px;bottom:32px;width:1px;background:var(--line2);}
  .stat .n{font-size:50px;font-weight:800;letter-spacing:-.03em;line-height:1;font-variant-numeric:tabular-nums;}
  .stat .n small{font-size:20px;font-weight:600;color:var(--ink3);margin-left:5px;position:relative;top:-1px;}
  .stat .l{margin-top:14px;font-size:18px;color:var(--ink3);letter-spacing:.05em;}
  .stat:nth-child(1) .n{color:var(--accent);}
  section{margin-top:76px;}
  .sec-head{display:flex;align-items:flex-end;justify-content:space-between;
    padding-bottom:20px;border-bottom:1px solid var(--line);}
  .sec-left{display:flex;align-items:baseline;gap:16px;}
  .sec-no{font-size:18px;font-weight:700;color:var(--accent);letter-spacing:.16em;}
  .sec-title{font-size:36px;font-weight:780;}
  .sec-note{font-size:17px;color:var(--ink3);letter-spacing:.04em;}
  .bars{margin-top:34px;}
  .brow{display:flex;align-items:center;gap:22px;padding:17px 0;}
  .brow+.brow{border-top:1px solid var(--line2);}
  .brow .d{flex:none;width:150px;font-size:19px;color:var(--ink3);font-variant-numeric:tabular-nums;}
  .brow .track{flex:1;height:14px;border-radius:7px;background:#F2F0EB;overflow:hidden;}
  .brow .track i{display:block;height:100%;border-radius:7px;background:#DBD7CE;}
  .brow .v{flex:none;width:74px;text-align:right;font-size:20px;font-weight:600;color:var(--ink3);font-variant-numeric:tabular-nums;}
  .brow.peak .d{color:var(--ink);font-weight:650;}
  .brow.peak .track i{background:var(--accent);}
  .brow.peak .v{color:var(--accent);font-weight:750;}
  .brow.zero .track{background:transparent;} .brow.zero .track i{display:none;}
  .brow.zero .v{font-weight:500;color:#C6C2BA;}
  .chart-cap{margin-top:22px;font-size:19px;color:var(--ink3);line-height:1.75;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:18px;
    padding:40px 44px 38px;box-shadow:0 1px 2px rgba(23,24,26,.03),0 12px 28px -22px rgba(23,24,26,.18);}
  .card+.card{margin-top:22px;}
  .c-head{display:flex;align-items:flex-start;gap:18px;}
  .c-no{flex:none;width:46px;height:46px;border-radius:12px;background:var(--accent-soft);
    color:var(--accent);font-size:21px;font-weight:800;display:flex;align-items:center;justify-content:center;}
  .c-title{font-size:31px;font-weight:770;line-height:1.35;}
  .c-meta{margin-top:9px;font-size:17px;color:var(--ink3);letter-spacing:.04em;}
  .c-body{margin-top:26px;padding-left:42px;}
  .p{position:relative;padding-left:22px;font-size:22px;line-height:1.82;color:var(--ink2);}
  .p+.p{margin-top:12px;}
  .p::before{content:"";position:absolute;left:2px;top:18px;width:5px;height:5px;border-radius:50%;background:#C9C5BC;}
  .p b{color:var(--ink);font-weight:650;} .p .hl{color:var(--accent);font-weight:650;}
  .q{margin-top:24px;padding:20px 26px;border-radius:12px;background:__QUOTEBG__;
    border-left:2px solid __QUOTEBAR__;font-size:21px;line-height:1.75;color:__QUOTETXT__;}
  .q em{font-style:normal;color:__QUOTEEM__;font-size:17px;margin-left:12px;}
  .tbl{margin-top:34px;width:100%;border-collapse:collapse;}
  .tbl th{text-align:left;font-size:16px;font-weight:650;color:var(--ink3);letter-spacing:.12em;
    padding:0 0 14px;border-bottom:1px solid var(--line);}
  .tbl td{padding:22px 0;font-size:20px;color:var(--ink2);border-bottom:1px solid var(--line2);
    line-height:1.6;vertical-align:top;}
  .tbl tr:last-child td{border-bottom:none;padding-bottom:0;}
  .tbl td:first-child{width:210px;padding-right:22px;color:var(--ink);font-weight:650;}
  .tbl td:nth-child(2){width:290px;padding-right:28px;}
  .tbl td.rig{font-size:19px;color:var(--ink2);}
  .wall{margin-top:36px;display:grid;grid-template-columns:1fr 1fr;gap:20px;}
  .wq{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:32px 34px 28px;}
  .wq p{font-size:23px;line-height:1.7;color:var(--ink);font-weight:600;}
  .wq .by{margin-top:18px;font-size:17px;color:var(--ink3);}
  .wq .by b{color:var(--ink2);font-weight:600;}
  .notice{margin-top:22px;background:#F1F3F1;border:1px solid #E2E6E1;border-radius:18px;padding:36px 42px;}
  .notice .nt{display:flex;align-items:center;gap:12px;font-size:19px;font-weight:700;color:#6B7A6E;letter-spacing:.1em;}
  .notice .nt i{width:7px;height:7px;border-radius:50%;background:#8A9A8C;}
  .notice p{margin-top:16px;font-size:22px;line-height:1.85;color:#4A4F4A;}
  .notice b{color:#2A2E2A;font-weight:650;}
  footer{margin-top:68px;padding:36px 0 68px;border-top:1px solid var(--line);}
  .f-row{display:flex;justify-content:space-between;align-items:flex-start;gap:40px;}
  .f-l{flex:1;font-size:17px;line-height:2.0;color:var(--ink3);}
  .f-l b{color:var(--ink2);font-weight:600;}
  .f-r{flex:none;text-align:right;font-size:17px;line-height:2.0;color:var(--ink3);white-space:nowrap;}
  .mark{margin-top:26px;display:flex;align-items:center;gap:14px;}
  .mark i{display:block;width:30px;height:1.5px;background:#D5D1C8;}
  .mark span{font-size:16px;color:#B0ABA2;letter-spacing:.22em;}
"""


def esc(s):
    return html.escape(s, quote=False)


def build(d, meta):
    css = (CSS.replace('__ACCENT__', d['accent']).replace('__SOFT__', d['soft'])
           .replace('__QUOTEBG__', d.get('qbg', '#FAF7F2'))
           .replace('__QUOTEBAR__', d.get('qbar', '#DCC3B2'))
           .replace('__QUOTETXT__', d.get('qtxt', '#5A4B41'))
           .replace('__QUOTEEM__', d.get('qem', '#9B8C80')))
    o = []
    o.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    o.append(f'<title>{esc(d["title"])} · 周记</title><style>{css}</style></head><body><div class="wrap">')
    o.append('<header><div class="kicker"><span>WEEKLY DIGEST</span><i></i>'
             f'<span style="color:#8B9096;letter-spacing:.16em;">{esc(d["kicker"])}</span></div>')
    o.append(f'<h1>{d["h1"]}</h1>')
    o.append(f'<div class="period"><b>{esc(meta["period"])}</b><span>{esc(meta["period_note"])}</span></div>')
    o.append(f'<p class="lede">{d["lede"]}</p></header>')

    o.append('<div class="stats">')
    for n, unit, label in d['stats']:
        o.append(f'<div class="stat"><div class="n">{n}<small>{unit}</small></div><div class="l">{label}</div></div>')
    o.append('</div>')

    o.append('<section><div class="sec-head"><div class="sec-left"><span class="sec-no">01</span>'
             '<span class="sec-title">这一周的声音</span></div><span class="sec-note">按日统计 · 条</span></div>')
    mx = max(v for _, _, v in d['bars']) or 1
    peak = max(range(len(d['bars'])), key=lambda i: d['bars'][i][2])
    o.append('<div class="bars">')
    for i, (day, wd, v) in enumerate(d['bars']):
        cls = 'peak' if i == peak else ('zero' if v == 0 else '')
        ratio = 0 if v == 0 else max(v / mx * 100, 2.2)
        shown = '—' if v == 0 else str(v)
        o.append(f'<div class="brow {cls}"><span class="d">{day}　{wd}</span>'
                 f'<span class="track"><i style="width:{ratio:.1f}%"></i></span><span class="v">{shown}</span></div>')
    o.append('</div>')
    o.append(f'<p class="chart-cap">{d["chart_cap"]}</p></section>')

    o.append('<section><div class="sec-head"><div class="sec-left"><span class="sec-no">02</span>'
             '<span class="sec-title">本周话题</span></div><span class="sec-note">按讨论热度</span></div>')
    for i, t in enumerate(d['topics'], 1):
        o.append('<div class="card" style="margin-top:32px;">' if i == 1 else '<div class="card">')
        o.append(f'<div class="c-head"><div class="c-no">{i:02d}</div><div>'
                 f'<div class="c-title">{t["title"]}</div><div class="c-meta">{esc(t["meta"])}</div></div></div>')
        o.append('<div class="c-body">')
        for p in t['bullets']:
            o.append(f'<p class="p">{p}</p>')
        o.append('</div>')
        if t.get('quote'):
            o.append(f'<div class="q">「{t["quote"][0]}」<em>{esc(t["quote"][1])}</em></div>')
        o.append('</div>')
    o.append('</section>')

    if d.get('table'):
        tb = d['table']
        o.append('<section><div class="sec-head"><div class="sec-left"><span class="sec-no">03</span>'
                 f'<span class="sec-title">{esc(tb["title"])}</span></div>'
                 f'<span class="sec-note">{esc(tb.get("note",""))}</span></div>')
        o.append('<table class="tbl"><tr>' + ''.join(f'<th>{esc(x)}</th>' for x in tb['header']) + '</tr>')
        for row in tb['rows']:
            o.append('<tr>' + ''.join(f'<td{" class=\"rig\"" if i == 2 else ""}>{c}</td>'
                                     for i, c in enumerate(row)) + '</tr>')
        o.append('</table></section>')

    o.append('<section><div class="sec-head"><div class="sec-left"><span class="sec-no">04</span>'
             '<span class="sec-title">这周说过的话</span></div><span class="sec-note">原文摘录</span></div>')
    o.append('<div class="wall">')
    for q, by in d['quotes']:
        o.append(f'<div class="wq"><p>「{q}」</p><div class="by"><b>{esc(by)}</b> · {esc(meta["quote_range"])}</div></div>')
    o.append('</div>')
    if d.get('notice'):
        o.append('<div class="notice"><div class="nt"><i></i><span>顺带一提</span></div>'
                 f'<p>{d["notice"]}</p></div>')
    o.append('</section>')

    o.append('<footer><div class="f-row"><div class="f-l">'
             f'<b>数据来源</b>　本机微信记录导出（按消息时间过滤 {esc(meta["date_filter"])}）<br>'
             f'<b>统计口径</b>　{d["foot_stat"]}<br>'
             f'<b>说明</b>　　{d["foot_note"]}</div>'
             f'<div class="f-r">制作　{esc(meta["made_at"])}<br>{esc(meta["week_no"])}</div></div>'
             '<div class="mark"><i></i><span>END OF WEEK</span></div></footer>')
    o.append('</div></body></html>')
    return '\n'.join(o)


def main():
    src = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_DATA
    if not src.exists():
        raise SystemExit(
            f'未找到周报数据：{src}\n'
            '数据文件应放在外部数据目录 ~/wechat-export/ 下，'
            '或用第一个参数指定路径 / 设置 WEEKLY_DATA 环境变量。'
        )
    outdir = (Path(sys.argv[2]).expanduser() if len(sys.argv) > 2
              else src.parent / 'cards')
    outdir.mkdir(parents=True, exist_ok=True)

    meta = json.loads(src.read_text(encoding='utf-8'))
    for d in meta['groups']:
        out = outdir / f'{meta["file_prefix"]}{d["key"]}.html'
        out.write_text(build(d, meta), encoding='utf-8')
        print(out)


if __name__ == '__main__':
    main()
