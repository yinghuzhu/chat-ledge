#!/usr/bin/env python3
"""生成适合单张转发的群周报分享卡 HTML（固定 1080x1440 竖版、浅色卡片风格）。

模板与数据分离：本文件只含渲染逻辑，真实聊天内容放在外部数据目录
（默认 ~/wechat-export/share_cards_data.json，不在本仓库中，请勿提交）。
数据格式：顶层 period / period_note / week_no / made_at / quote_range /
file_prefix，以及 cards 数组；每张卡片含 key / no / title / accent / soft /
lede / stats / bars / points / quote。

用法：
    python3 share_cards.py                       # 读默认外部数据
    python3 share_cards.py /path/to/data.json   # 指定数据文件
    python3 share_cards.py data.json /path/out  # 指定输出目录

生成 HTML 后转 PNG：
    node shot_long.js <input.html> <output.png> 1080 2 0 1440
"""
import html
import json
import os
import sys
from pathlib import Path

DATA_HOME = Path(os.environ.get('WECHAT_EXPORT_HOME', Path.home() / 'wechat-export'))
DEFAULT_DATA = Path(os.environ.get('SHARE_CARDS_DATA', DATA_HOME / 'share_cards_data.json'))
DEFAULT_OUT = DATA_HOME / 'cards'
BAR_MAX = 100

CSS = """
  *{margin:0;padding:0;box-sizing:border-box;}
  :root{
    --paper:#F8F6F1; --card:#FFFFFF; --ink:#17181A; --ink2:#4E5257; --ink3:#8B9096;
    --line:#E8E5DE; --line2:#F0EEE9; --track:#E7E3DB;
    --accent:__ACCENT__; --soft:__SOFT__;
  }
  html,body{background:var(--paper);}
  body{width:1080px;height:1440px;overflow:hidden;padding:36px;
    font-family:"PingFang SC","Hiragino Sans GB","Noto Sans SC","Microsoft YaHei",sans-serif;
    color:var(--ink);-webkit-font-smoothing:antialiased;}
  .card{width:100%;height:100%;background:var(--card);border:1px solid var(--line);
    border-radius:26px;padding:54px 60px;display:flex;flex-direction:column;
    justify-content:space-between;}

  .kicker{display:flex;align-items:center;justify-content:space-between;
    font-size:17px;letter-spacing:.24em;color:var(--accent);font-weight:600;}
  .kicker .no{color:var(--ink3);font-weight:500;letter-spacing:.12em;}
  h1{margin-top:24px;font-size:44px;line-height:1.3;font-weight:800;letter-spacing:-.015em;}
  .period{margin-top:16px;font-size:19px;color:var(--ink3);letter-spacing:.05em;}
  .lede{margin-top:26px;font-size:24px;line-height:1.75;color:var(--ink2);}

  .stats{display:grid;grid-template-columns:repeat(3,1fr);
    border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:26px 0;}
  .stat+.stat{border-left:1px solid var(--line);padding-left:36px;}
  .stat .n{font-size:44px;font-weight:800;line-height:1;color:var(--ink);
    letter-spacing:-.02em;font-variant-numeric:tabular-nums;}
  .stat:first-child .n{color:var(--accent);}
  .stat .n small{font-size:18px;font-weight:600;color:var(--ink3);margin-left:6px;}
  .stat .l{margin-top:12px;font-size:16px;color:var(--ink3);letter-spacing:.04em;}

  .sec-h{font-size:17px;color:var(--accent);letter-spacing:.16em;font-weight:600;margin-bottom:16px;}
  .bars-row{display:flex;align-items:flex-end;gap:11px;height:__BARROW__px;}
  .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;
    align-items:center;height:100%;}
  .bar{width:100%;background:var(--track);border-radius:5px 5px 2px 2px;
    min-height:3px;flex:none;}
  .col.peak .bar{background:var(--accent);}
  .lab{margin-top:11px;font-size:15px;color:var(--ink3);font-variant-numeric:tabular-nums;}
  .col.peak .lab{color:var(--accent);font-weight:650;}

  .pt{display:flex;gap:18px;padding:11px 0;}
  .pt+.pt{border-top:1px solid var(--line2);}
  .pt .i{flex:none;width:32px;padding-top:5px;font-size:16px;font-weight:700;
    color:var(--accent);letter-spacing:.04em;}
  .pt .t{font-size:23px;font-weight:650;line-height:1.35;}
  .pt .d{margin-top:6px;font-size:17px;line-height:1.6;color:var(--ink3);}

  .quote{background:var(--soft);border-left:3px solid var(--accent);
    border-radius:0 12px 12px 0;padding:22px 28px;}
  .quote .q{font-size:25px;line-height:1.6;font-weight:650;}
  .quote .a{margin-top:10px;text-align:right;font-size:16px;color:var(--ink3);}

  footer{display:flex;justify-content:space-between;gap:20px;
    border-top:1px solid var(--line);padding-top:18px;
    font-size:15px;color:var(--ink3);letter-spacing:.03em;}
"""


def esc(s):
    return html.escape(str(s), quote=False)


def build(c, meta, total):
    css = (CSS.replace('__ACCENT__', esc(c['accent']))
              .replace('__SOFT__', esc(c['soft']))
              .replace('__BARROW__', str(BAR_MAX + 36)))
    mx = max((v for _, v in c['bars']), default=1) or 1

    stats = []
    for num, unit, label in c['stats']:
        stats.append(
            '<div class="stat"><div class="n">%s<small>%s</small></div>'
            '<div class="l">%s</div></div>' % (esc(num), esc(unit), esc(label))
        )

    bars = []
    for label, val in c['bars']:
        h = 3 if val == 0 else max(4, round(val / mx * BAR_MAX))
        peak = ' peak' if val == mx else ''
        bars.append(
            '<div class="col%s"><div class="bar" style="height:%dpx"></div>'
            '<div class="lab">%s</div></div>' % (peak, h, esc(label))
        )

    points = []
    for i, (title, desc) in enumerate(c['points'], 1):
        points.append(
            '<div class="pt"><div class="i">%02d</div><div><div class="t">%s</div>'
            '<div class="d">%s</div></div></div>' % (i, esc(title), esc(desc))
        )

    q_text, q_author = c['quote']
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>%(title)s</title>
<style>%(css)s</style></head>
<body><div class="card">
  <div class="top">
    <div class="kicker"><span>WEEKLY · 群周记</span><span class="no">%(no)s / %(total)02d</span></div>
    <h1>%(title)s</h1>
    <div class="period">2026 · %(period)s　%(period_note)s</div>
    <div class="lede">%(lede)s</div>
  </div>
  <div class="stats">%(stats)s</div>
  <div class="bars">
    <div class="sec-h">每日消息量</div>
    <div class="bars-row">%(bars)s</div>
  </div>
  <div class="points">
    <div class="sec-h">本周三件事</div>
    %(points)s
  </div>
  <div class="quote">
    <div class="q">「%(q)s」</div>
    <div class="a">— %(author)s</div>
  </div>
  <footer>
    <div>本机微信记录导出 · 按消息时间过滤 %(qr)s</div>
    <div>%(made)s · %(week)s</div>
  </footer>
</div></body></html>
""" % {
        'title': esc(c['title']), 'css': css, 'no': esc(c['no']), 'total': total,
        'period': esc(meta['period']), 'period_note': esc(meta['period_note']),
        'lede': esc(c['lede']), 'stats': ''.join(stats), 'bars': ''.join(bars),
        'points': ''.join(points), 'q': esc(q_text), 'author': esc(q_author),
        'qr': esc(meta['quote_range']), 'made': esc(meta['made_at']),
        'week': esc(meta['week_no']),
    }


def main():
    src = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_DATA
    out = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else DEFAULT_OUT
    if not src.exists():
        raise SystemExit('未找到数据文件：%s\n请把数据放在仓库外的目录后再运行。' % src)
    meta = json.loads(src.read_text(encoding='utf-8'))
    out.mkdir(parents=True, exist_ok=True)
    total = len(meta['cards'])
    for c in meta['cards']:
        path = out / ('%s%s.html' % (meta['file_prefix'], c['key']))
        path.write_text(build(c, meta, total), encoding='utf-8')
        print(path)


if __name__ == '__main__':
    main()
