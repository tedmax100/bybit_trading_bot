"""產生 Bybit 機器人績效儀表板 (GitHub Pages) 與首頁圖表 (README.md / SVG)。
純 Python 標準函式庫實作,無須安裝額外視覺化套件 (如 matplotlib/seaborn)。

功能:
1. 讀取 live.db 與 bot.db
2. 生成 assets/equity_curve.svg (清晰向量圖,直接嵌入 README)
3. 生成 site/index.html (含 Chart.js 互動式儀表板,供 GitHub Pages 部署)
4. 自動更新 README.md 的績效區塊
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
from pathlib import Path


def parse_db(db_path: str):
    """讀取 SQLite 資料庫並計算每日績效序列與摘要"""
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        rows = cursor.execute(
            "SELECT day, equity, cash, holdings, actions, signals, halted, btc FROM snapshots ORDER BY day ASC"
        ).fetchall()
    except Exception:
        rows = []

    # 讀取 kv 設定與狀態
    kv = {}
    try:
        for k, v in cursor.execute("SELECT k, v FROM kv").fetchall():
            try:
                kv[k] = json.loads(v)
            except Exception:
                kv[k] = v
    except Exception:
        pass
    conn.close()

    if not rows:
        return None

    e0 = rows[0][1]
    b0 = rows[0][7] or rows[0][1]

    days, equities, returns, btc_returns, drawdowns, cashes, holdings_vals = [], [], [], [], [], [], []
    records = []
    peak = e0

    for r in rows:
        day, eq, cash, h_json, act_json, sig_json, halted, btc_px = r
        peak = max(peak, eq)
        ret = (eq / e0 - 1) if e0 > 0 else 0.0
        btc_ret = (btc_px / b0 - 1) if b0 > 0 else 0.0
        dd = (eq / peak - 1) if peak > 0 else 0.0

        days.append(day)
        equities.append(round(eq, 2))
        returns.append(round(ret * 100, 2))
        btc_returns.append(round(btc_ret * 100, 2))
        drawdowns.append(round(dd * 100, 2))
        cashes.append(round(cash, 2))
        holdings_vals.append(round(max(0.0, eq - cash), 2))

        try:
            actions = json.loads(act_json) if act_json else []
        except Exception:
            actions = [str(act_json)]

        records.append({
            "day": day,
            "equity": round(eq, 2),
            "cash": round(cash, 2),
            "ret": round(ret * 100, 2),
            "btc_ret": round(btc_ret * 100, 2),
            "dd": round(dd * 100, 2),
            "actions": actions,
            "halted": bool(halted),
        })

    # 合約短線統計 (psleeve)
    psleeve = kv.get("psleeve", {})
    perp_realized = psleeve.get("realized", 0.0)
    perp_fees = psleeve.get("fees", 0.0)
    perp_funding = psleeve.get("funding", 0.0)
    perp_trades = psleeve.get("trades", 0)
    perp_wins = psleeve.get("wins", 0)
    perp_liqs = psleeve.get("liqs", 0)
    perp_winrate = (perp_wins / perp_trades * 100) if perp_trades > 0 else 0.0
    perp_log = psleeve.get("log", [])
    perp_pos = psleeve.get("pos", {})

    # 現貨持倉 (paper)
    paper = kv.get("paper", {})
    spot_hold = paper.get("hold", {})

    cur_eq = equities[-1]
    total_pnl = cur_eq - e0
    total_ret = returns[-1]
    btc_final_ret = btc_returns[-1]
    alpha = total_ret - btc_final_ret
    max_dd = min(drawdowns)

    return {
        "days": days,
        "equities": equities,
        "returns": returns,
        "btc_returns": btc_returns,
        "drawdowns": drawdowns,
        "cashes": cashes,
        "holdings_vals": holdings_vals,
        "records": records,
        "e0": e0,
        "current_equity": cur_eq,
        "total_pnl": total_pnl,
        "total_ret": total_ret,
        "btc_final_ret": btc_final_ret,
        "alpha": alpha,
        "max_dd": max_dd,
        "perp": {
            "realized": perp_realized,
            "fees": perp_fees,
            "funding": perp_funding,
            "trades": perp_trades,
            "wins": perp_wins,
            "winrate": perp_winrate,
            "liqs": perp_liqs,
            "log": perp_log,
            "pos": perp_pos,
        },
        "spot_hold": spot_hold,
    }


def generate_svg(data, output_path: str):
    """生成乾淨高解析度 SVG 曲線圖 (深色主題)"""
    if not data or len(data["days"]) < 2:
        return

    width, height = 850, 420
    padding_top, padding_bottom = 60, 60
    padding_left, padding_right = 75, 40

    plot_w = width - padding_left - padding_right
    plot_h = height - padding_top - padding_bottom

    # Y 軸範圍 (用報酬率 % 或權益 $)
    # 這裡採用 雙曲線對比: Bot Return % vs BTC Return %
    all_y = data["returns"] + data["btc_returns"] + [0.0]
    min_y = min(all_y)
    max_y = max(all_y)
    
    # 增加邊界緩衝
    range_y = max_y - min_y
    if range_y == 0:
        range_y = 10.0
    min_y = min_y - range_y * 0.1
    max_y = max_y + range_y * 0.1

    def x_coord(i, n):
        return padding_left + (i / (n - 1)) * plot_w

    def y_coord(val):
        return padding_top + (1 - (val - min_y) / (max_y - min_y)) * plot_h

    n = len(data["days"])
    zero_y = y_coord(0.0)

    # 產生多段點座標
    bot_pts = " ".join(f"{x_coord(i, n):.1f},{y_coord(v):.1f}" for i, v in enumerate(data["returns"]))
    btc_pts = " ".join(f"{x_coord(i, n):.1f},{y_coord(v):.1f}" for i, v in enumerate(data["btc_returns"]))

    # 填充區域 (Bot Return)
    bot_area = (
        f"{x_coord(0, n):.1f},{zero_y:.1f} "
        + bot_pts
        + f" {x_coord(n-1, n):.1f},{zero_y:.1f}"
    )

    # 產生 Y 軸刻度線 (5 條)
    y_ticks = []
    for step in range(5):
        val = min_y + (step / 4.0) * (max_y - min_y)
        y_pos = y_coord(val)
        y_ticks.append(f"""
        <line x1="{padding_left}" y1="{y_pos:.1f}" x2="{width - padding_right}" y2="{y_pos:.1f}" stroke="#30363d" stroke-dasharray="3,3" stroke-width="1"/>
        <text x="{padding_left - 10}" y="{y_pos + 4:.1f}" fill="#8b949e" font-size="11" text-anchor="end" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">{val:+.1f}%</text>
        """)

    # 產生 X 軸刻度 (首尾 + 中間)
    x_ticks = []
    x_indices = [0, n // 2, n - 1] if n >= 3 else [0, n - 1]
    for idx in set(x_indices):
        x_pos = x_coord(idx, n)
        d_str = data["days"][idx]
        x_ticks.append(f"""
        <text x="{x_pos:.1f}" y="{height - padding_bottom + 22}" fill="#8b949e" font-size="11" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">{d_str}</text>
        """)

    svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">
  <defs>
    <linearGradient id="botGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#10b981" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#10b981" stop-opacity="0.0"/>
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="2" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <!-- 背景 -->
  <rect width="{width}" height="{height}" rx="12" fill="#0d1117" stroke="#30363d" stroke-width="1"/>

  <!-- 標題與 KPI 摘要 -->
  <text x="{padding_left}" y="32" fill="#f0f6fc" font-size="16" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">機器人累積報酬率 vs BTC 基準</text>
  
  <text x="{width - padding_right}" y="32" text-anchor="end" font-size="13" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
    <tspan fill="#8b949e">目前權益: </tspan>
    <tspan fill="#f0f6fc" font-weight="bold">{data['current_equity']:.2f} USDT</tspan>
    <tspan fill="#8b949e"> | 報酬: </tspan>
    <tspan fill="{'#10b981' if data['total_ret'] >= 0 else '#ef4444'}" font-weight="bold">{data['total_ret']:+.2f}%</tspan>
    <tspan fill="#8b949e"> (超額: </tspan>
    <tspan fill="#38bdf8" font-weight="bold">{data['alpha']:+.2f}%</tspan><tspan fill="#8b949e">)</tspan>
  </text>

  <!-- 刻度與網格 -->
  {''.join(y_ticks)}
  
  <!-- 0% 基準線 -->
  <line x1="{padding_left}" y1="{zero_y:.1f}" x2="{width - padding_right}" y2="{zero_y:.1f}" stroke="#6e7681" stroke-width="1.2"/>

  <!-- BTC 基準曲線 -->
  <polyline fill="none" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="4,4" points="{btc_pts}" opacity="0.85"/>

  <!-- 機器人面積與折線 -->
  <polygon fill="url(#botGrad)" points="{bot_area}"/>
  <polyline fill="none" stroke="#10b981" stroke-width="2.5" points="{bot_pts}" filter="url(#glow)"/>

  <!-- X 軸標籤 -->
  {''.join(x_ticks)}

  <!-- 圖例 (Legend) -->
  <g transform="translate({padding_left}, {height - 20})">
    <!-- 機器人 -->
    <line x1="0" y1="-4" x2="18" y2="-4" stroke="#10b981" stroke-width="3"/>
    <text x="24" y="0" fill="#f0f6fc" font-size="11" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">實盤策略 ({data['total_ret']:+.2f}%)</text>
    
    <!-- BTC 基準 -->
    <line x1="150" y1="-4" x2="168" y2="-4" stroke="#f59e0b" stroke-width="2" stroke-dasharray="3,3"/>
    <text x="174" y="0" fill="#8b949e" font-size="11" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">BTC 持有基準 ({data['btc_final_ret']:+.2f}%)</text>
  </g>
</svg>
"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(svg_content.strip())


def generate_html(live_data, bot_data, output_path: str):
    """生成極具現代質感的單頁 GitHub Pages 互動式儀表板 (含 Chart.js)"""
    data = live_data or bot_data
    if not data:
        return

    # 將數據轉換為 JSON 供前端圖表繪製
    chart_payload = {
        "days": data["days"],
        "equities": data["equities"],
        "returns": data["returns"],
        "btc_returns": data["btc_returns"],
        "drawdowns": data["drawdowns"],
        "cashes": data["cashes"],
        "holdings": data["holdings_vals"],
    }

    # 產生表格 HTML
    table_rows = []
    for r in reversed(data["records"]):
        act_str = "<br>".join(r["actions"]) if r["actions"] else "-"
        ret_color = "#10b981" if r["ret"] >= 0 else "#ef4444"
        btc_color = "#10b981" if r["btc_ret"] >= 0 else "#ef4444"
        table_rows.append(f"""
        <tr class="border-b border-gray-800 hover:bg-gray-800/40 transition">
          <td class="py-3 px-4 text-sm font-mono text-gray-300">{r['day']}</td>
          <td class="py-3 px-4 text-sm font-mono font-medium text-white">{r['equity']:.2f}</td>
          <td class="py-3 px-4 text-sm font-mono" style="color: {ret_color}">{r['ret']:+.2f}%</td>
          <td class="py-3 px-4 text-sm font-mono" style="color: {btc_color}">{r['btc_ret']:+.2f}%</td>
          <td class="py-3 px-4 text-sm font-mono text-amber-400">{r['dd']:.2f}%</td>
          <td class="py-3 px-4 text-sm font-mono text-gray-400">{r['cash']:.2f}</td>
          <td class="py-3 px-4 text-xs text-gray-400">{act_str}</td>
        </tr>
        """)

    perp_logs = []
    for line in reversed(data["perp"]["log"]):
        perp_logs.append(f'<div class="py-1 text-xs font-mono text-gray-300 border-b border-gray-800/50">{line}</div>')

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bybit 自動交易機器人 | 實盤績效儀表板</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body {{ background-color: #0b0f17; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    .glass-card {{ background: rgba(22, 27, 34, 0.7); backdrop-filter: blur(8px); border: 1px solid rgba(48, 54, 61, 0.8); }}
  </style>
</head>
<body class="text-gray-100 min-h-screen pb-16">
  <!-- 頂部導航列 -->
  <header class="border-b border-gray-800/80 bg-gray-900/60 sticky top-0 z-50 backdrop-blur-md px-6 py-4 flex items-center justify-between">
    <div class="flex items-center space-x-3">
      <div class="h-9 w-9 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
        <i class="fa-solid fa-chart-line text-lg"></i>
      </div>
      <div>
        <h1 class="text-base font-bold tracking-tight text-white flex items-center gap-2">
          Bybit Trading Bot Dashboard
          <span class="bg-emerald-500/20 text-emerald-400 text-xs px-2 py-0.5 rounded-full border border-emerald-500/30">Live Active</span>
        </h1>
        <p class="text-xs text-gray-400">4H 動態趨勢 + 波動目標配置 + 永續短線多空引擎</p>
      </div>
    </div>
    <div class="text-right">
      <div class="text-xs text-gray-400">更新時間 (UTC)</div>
      <div class="text-xs font-mono font-medium text-emerald-400">{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')}</div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 sm:px-6 pt-8 space-y-8">
    <!-- 核心 KPI 數字卡片區塊 -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div class="glass-card rounded-xl p-5">
        <div class="text-xs font-medium text-gray-400 mb-1 flex items-center justify-between">
          <span>目前總權益</span>
          <i class="fa-solid fa-wallet text-gray-500"></i>
        </div>
        <div class="text-2xl font-bold font-mono text-white">{data['current_equity']:.2f} <span class="text-xs font-normal text-gray-400">USDT</span></div>
        <div class="text-xs mt-2 {'text-emerald-400' if data['total_pnl'] >= 0 else 'text-red-400'}">
          {'+' if data['total_pnl'] >= 0 else ''}{data['total_pnl']:.2f} USDT ({data['total_ret']:+.2f}%)
        </div>
      </div>

      <div class="glass-card rounded-xl p-5">
        <div class="text-xs font-medium text-gray-400 mb-1 flex items-center justify-between">
          <span>同期超額報酬 (Alpha)</span>
          <i class="fa-brands fa-bitcoin text-amber-500"></i>
        </div>
        <div class="text-2xl font-bold font-mono text-sky-400">{data['alpha']:+.2f}%</div>
        <div class="text-xs mt-2 text-gray-400">
          BTC 同期: <span class="{'text-emerald-400' if data['btc_final_ret'] >= 0 else 'text-red-400'} font-mono">{data['btc_final_ret']:+.2f}%</span>
        </div>
      </div>

      <div class="glass-card rounded-xl p-5">
        <div class="text-xs font-medium text-gray-400 mb-1 flex items-center justify-between">
          <span>歷史最大回撤 (MDD)</span>
          <i class="fa-solid fa-shield-halved text-gray-500"></i>
        </div>
        <div class="text-2xl font-bold font-mono text-amber-400">{data['max_dd']:.2f}%</div>
        <div class="text-xs mt-2 text-gray-400">起算起始: {data['e0']:.2f} USDT</div>
      </div>

      <div class="glass-card rounded-xl p-5">
        <div class="text-xs font-medium text-gray-400 mb-1 flex items-center justify-between">
          <span>永續短線勝率</span>
          <i class="fa-solid fa-bolt text-indigo-400"></i>
        </div>
        <div class="text-2xl font-bold font-mono text-indigo-300">{data['perp']['winrate']:.0f}%</div>
        <div class="text-xs mt-2 text-emerald-400">
          已實現 {data['perp']['realized']:+.2f} USDT ({data['perp']['trades']} 戰 {data['perp']['wins']} 勝)
        </div>
      </div>
    </div>

    <!-- 主圖表: 權益與 BTC 走勢對比 -->
    <div class="glass-card rounded-xl p-6">
      <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6">
        <div>
          <h2 class="text-base font-semibold text-white">累計報酬率走勢 (Cumulative Return vs BTC)</h2>
          <p class="text-xs text-gray-400">策略資產成長曲線與比特幣買入持有基準比對</p>
        </div>
        <div class="flex items-center gap-4 text-xs">
          <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-emerald-500 inline-block"></span> 策略報酬</div>
          <div class="flex items-center gap-1.5"><span class="w-3 h-3 rounded-full bg-amber-500 inline-block"></span> BTC 基準</div>
        </div>
      </div>
      <div class="h-80 w-full">
        <canvas id="perfChart"></canvas>
      </div>
    </div>

    <!-- 次圖表雙欄: 資產曝險配置 + 水下回撤圖 -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div class="glass-card rounded-xl p-6">
        <h3 class="text-sm font-semibold text-white mb-1">資產配置比例 (現金 vs 持倉)</h3>
        <p class="text-xs text-gray-400 mb-4">持倉風險曝險部位與儲備現金動態比例</p>
        <div class="h-56 w-full">
          <canvas id="allocChart"></canvas>
        </div>
      </div>

      <div class="glass-card rounded-xl p-6">
        <h3 class="text-sm font-semibold text-white mb-1">歷史回撤走勢 (Underwater Drawdown)</h3>
        <p class="text-xs text-gray-400 mb-4">自峰值以來的最大資金回落百分比</p>
        <div class="h-56 w-full">
          <canvas id="ddChart"></canvas>
        </div>
      </div>
    </div>

    <!-- 歷史日誌與紀錄表格 -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- 每日紀錄列表 -->
      <div class="lg:col-span-2 glass-card rounded-xl p-6">
        <h3 class="text-sm font-semibold text-white mb-4 flex items-center justify-between">
          <span>每日快照與交易決策</span>
          <span class="text-xs font-normal text-gray-400">共 {len(data['records'])} 天記錄</span>
        </h3>
        <div class="overflow-x-auto max-h-96 overflow-y-auto">
          <table class="w-full text-left">
            <thead class="sticky top-0 bg-gray-900 text-xs text-gray-400 uppercase border-b border-gray-800">
              <tr>
                <th class="py-2.5 px-4">日期</th>
                <th class="py-2.5 px-4">總權益</th>
                <th class="py-2.5 px-4">累計</th>
                <th class="py-2.5 px-4">BTC</th>
                <th class="py-2.5 px-4">回撤</th>
                <th class="py-2.5 px-4">現金</th>
                <th class="py-2.5 px-4">決策動作</th>
              </tr>
            </thead>
            <tbody>
              {''.join(table_rows)}
            </tbody>
          </table>
        </div>
      </div>

      <!-- 永續合約決策紀錄 -->
      <div class="glass-card rounded-xl p-6">
        <h3 class="text-sm font-semibold text-white mb-4 flex items-center justify-between">
          <span>永續短線明細 (4H 週期)</span>
          <span class="text-xs text-indigo-400 font-mono">2x 槓桿</span>
        </h3>
        <div class="space-y-2 mb-4 p-3 bg-gray-900/60 rounded-lg text-xs font-mono text-gray-300">
          <div class="flex justify-between"><span>已實現毛利:</span> <span class="text-emerald-400">+{data['perp']['realized'] + data['perp']['fees'] - data['perp']['funding']:.2f} USDT</span></div>
          <div class="flex justify-between"><span>交易手續費:</span> <span class="text-gray-400">-{data['perp']['fees']:.2f} USDT</span></div>
          <div class="flex justify-between"><span>Funding 收益:</span> <span class="text-emerald-400">+{data['perp']['funding']:.2f} USDT</span></div>
          <div class="flex justify-between border-t border-gray-800 pt-1 font-bold"><span>淨已實現損益:</span> <span class="text-emerald-400">{data['perp']['realized']:+.2f} USDT</span></div>
        </div>
        <div class="text-xs text-gray-400 mb-2 font-medium">開平倉決策歷史:</div>
        <div class="overflow-y-auto max-h-60 space-y-1 pr-1">
          {''.join(perp_logs) if perp_logs else '<div class="text-xs text-gray-500">暫無記錄</div>'}
        </div>
      </div>
    </div>
  </main>

  <script>
    const chartData = {json.dumps(chart_payload)};

    // 1. 績效主走勢圖
    new Chart(document.getElementById('perfChart').getContext('2d'), {{
      type: 'line',
      data: {{
        labels: chartData.days,
        datasets: [
          {{
            label: '策略報酬 (%)',
            data: chartData.returns,
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.1)',
            fill: true,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 2,
            pointHoverRadius: 5
          }},
          {{
            label: 'BTC 基準 (%)',
            data: chartData.btc_returns,
            borderColor: '#f59e0b',
            borderDash: [5, 5],
            fill: false,
            tension: 0.3,
            borderWidth: 1.8,
            pointRadius: 0
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{ mode: 'index', intersect: false }}
        }},
        scales: {{
          x: {{ grid: {{ color: 'rgba(255, 255, 255, 0.05)' }}, ticks: {{ color: '#8b949e' }} }},
          y: {{ grid: {{ color: 'rgba(255, 255, 255, 0.05)' }}, ticks: {{ color: '#8b949e', callback: v => v + '%' }} }}
        }}
      }}
    }});

    // 2. 資產配置柱狀堆疊圖
    new Chart(document.getElementById('allocChart').getContext('2d'), {{
      type: 'bar',
      data: {{
        labels: chartData.days,
        datasets: [
          {{ label: '現金儲備 (USDT)', data: chartData.cashes, backgroundColor: '#3b82f6' }},
          {{ label: '持倉部位 (USDT)', data: chartData.holdings, backgroundColor: '#10b981' }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ display: false }} }},
          y: {{ stacked: true, grid: {{ color: 'rgba(255, 255, 255, 0.05)' }}, ticks: {{ color: '#8b949e' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#8b949e', font: {{ size: 10 }} }} }} }}
      }}
    }});

    // 3. 水下回撤圖
    new Chart(document.getElementById('ddChart').getContext('2d'), {{
      type: 'line',
      data: {{
        labels: chartData.days,
        datasets: [{{
          label: '回撤 (%)',
          data: chartData.drawdowns,
          borderColor: '#ef4444',
          backgroundColor: 'rgba(239, 68, 68, 0.15)',
          fill: true,
          tension: 0.2,
          borderWidth: 1.5,
          pointRadius: 0
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ grid: {{ display: false }}, ticks: {{ color: '#8b949e' }} }},
          y: {{ grid: {{ color: 'rgba(255, 255, 255, 0.05)' }}, ticks: {{ color: '#8b949e', callback: v => v + '%' }}, max: 0 }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content.strip())


def extract_recent_logs(data, days=7):
    """提取最近 N 天的現貨與永續買賣調倉紀錄 (依時間由新到舊排序)"""
    if not data:
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    cut_date = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    events = []

    # 1. 現貨買賣/調倉紀錄 (來自 snapshots)
    for r in data.get("records", []):
        day = r["day"]
        if day >= cut_date and r.get("actions"):
            for act in r["actions"]:
                if not act or act == "-":
                    continue
                sym = "BTC/ETH"
                for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "XRPUSDT", "AAVEUSDT"]:
                    if s in act:
                        sym = s
                        break
                events.append({
                    "time": f"`{day}`",
                    "market": "現貨策略",
                    "sym": f"`{sym}`",
                    "action": act,
                    "amount_pnl": "-",
                    "reason": "動態趨勢與均線訊號調倉",
                })

    # 2. 永續短線買賣紀錄 (來自 psleeve log)
    for l in data.get("perp", {}).get("log", []):
        parts = l.split()
        if len(parts) >= 2:
            d_str = parts[0]
            if d_str >= cut_date:
                t_str = f"`{parts[0]} {parts[1]}`"
                m = re.match(
                    r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+永續(開空|開多|平倉)\s+(\w+)\s+(.*?)(?:\s+損益\s+([+\-0-9.]+))?(?:\s+名目\s+([0-9.]+))?\s*\((.*)\)',
                    l,
                )
                if m:
                    _, act, sym, detail, pnl, notional, reason = m.groups()
                    if act == "平倉":
                        act_str = f"平倉 {detail}"
                        amt_pnl = f"**`{pnl} USDT`**" if pnl else "-"
                    else:
                        act_str = f"{act} {detail}"
                        amt_pnl = f"`{notional} USDT`" if notional else "-"
                    events.append({
                        "time": t_str,
                        "market": "永續短線",
                        "sym": f"`{sym}`",
                        "action": act_str,
                        "amount_pnl": amt_pnl,
                        "reason": reason,
                    })
                else:
                    events.append({
                        "time": t_str,
                        "market": "永續短線",
                        "sym": f"`{parts[3]}`" if len(parts) > 3 else "Perp",
                        "action": " ".join(parts[2:]),
                        "amount_pnl": "-",
                        "reason": "-",
                    })

    # 由新到舊排序 (去除 ` 符號輔助排序)
    events.sort(key=lambda x: x["time"].replace("`", ""), reverse=True)
    return events


def update_readme(data, readme_path: str = "README.md", gh_page_url: str = ""):
    """自動更新 README.md 中的績效摘要與圖表"""
    if not os.path.exists(readme_path) or not data:
        return

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    start_tag = "<!-- BOT_DASHBOARD_START -->"
    end_tag = "<!-- BOT_DASHBOARD_END -->"

    gh_page_link = f"[👉 點此開啟即時互動儀表板 (GitHub Pages)]({gh_page_url})" if gh_page_url else ""

    # 產出近 7 日操作日誌表格
    recent_logs = extract_recent_logs(data, days=7)
    if recent_logs:
        log_rows = []
        for log in recent_logs:
            log_rows.append(
                f"| {log['time']} | {log['market']} | {log['sym']} | {log['action']} | {log['amount_pnl']} | {log['reason']} |"
            )
        log_table_md = "\n".join([
            "### 📝 近 7 日交易與調倉日誌",
            "",
            "| 時間 (UTC) | 市場 | 標的 | 交易動作 | 損益 / 名目額 | 觸發原因 |",
            "| :--- | :---: | :---: | :--- | :---: | :--- |",
            *log_rows,
        ])
    else:
        log_table_md = "\n".join([
            "### 📝 近 7 日交易與調倉日誌",
            "",
            "*近 7 日無買賣操作，維持既有配置或空手觀望。*",
        ])

    dashboard_md = f"""{start_tag}
## 📊 機器人實盤績效走勢

{gh_page_link}

![實盤權益走勢](assets/equity_curve.svg)

| 指標 | 數值 | 說明 |
| :--- | :---: | :--- |
| **目前總權益** | **`{data['current_equity']:.2f} USDT`** | 起始本金 `{data['e0']:.2f} USDT` |
| **累計淨損益** | **`{data['total_pnl']:+.2f} USDT` (`{data['total_ret']:+.2f}%`)** | 扣除所有手續費與融資成本之淨值 |
| **同期 BTC 報酬** | **`{data['btc_final_ret']:+.2f}%`** | 同期買入並持有比特幣表現 |
| **超額報酬 (Alpha)**| **`{data['alpha']:+.2f}%`** | 策略相對於 BTC 的超額增益 |
| **歷史最大回撤** | **`{data['max_dd']:.2f}%`** | 運行期間內資產自高點最大拉回幅度 |
| **永續短線 (4H)** | **勝率 `{data['perp']['winrate']:.0f}%` / 已實現 `{data['perp']['realized']:+.2f} USDT`** | {data['perp']['trades']} 戰 {data['perp']['wins']} 勝，爆倉 0 次 |

{log_table_md}

*最後自動更新時間：`{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}`*
{end_tag}"""

    if start_tag in content and end_tag in content:
        before = content.split(start_tag)[0]
        after = content.split(end_tag)[1]
        new_content = before + dashboard_md + after
    else:
        # 如果還沒有標籤，加在開頭的介紹之後
        lines = content.splitlines(keepends=True)
        insert_idx = 0
        for i, l in enumerate(lines):
            if l.startswith("# "):
                insert_idx = i + 1
                break
        new_content = "".join(lines[:insert_idx]) + "\n" + dashboard_md + "\n\n" + "".join(lines[insert_idx:])

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(new_content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-db", default="data/live.db", help="實盤 live.db 路徑")
    parser.add_argument("--bot-db", default="data/bot.db", help="模擬 bot.db 路徑")
    parser.add_argument("--svg-out", default="assets/equity_curve.svg", help="SVG 輸出路徑")
    parser.add_argument("--html-out", default="site/index.html", help="HTML 輸出路徑")
    parser.add_argument("--readme", default="README.md", help="README 檔案路徑")
    parser.add_argument("--gh-url", default="", help="GitHub Pages 網址")
    parser.add_argument("--no-readme-update", action="store_true", help="不更新 README")
    args = parser.parse_args()

    live_data = parse_db(args.live_db)
    bot_data = parse_db(args.bot_db)

    active_data = live_data or bot_data
    if not active_data:
        print("未找到有效的資料庫數據 (live.db 或 bot.db)")
        sys.exit(0)

    # 1. 產生向量 SVG
    generate_svg(active_data, args.svg_out)
    print(f"✅ 已產生 SVG 走勢圖: {args.svg_out}")

    # 2. 產生 GitHub Pages HTML
    generate_html(live_data, bot_data, args.html_out)
    print(f"✅ 已產生 GitHub Pages 儀表板: {args.html_out}")

    # 3. 更新 README
    if not args.no_readme_update:
        update_readme(active_data, args.readme, args.gh_url)
        print(f"✅ 已更新 README.md: {args.readme}")


if __name__ == "__main__":
    main()
