from __future__ import annotations

import csv
import html
import json
from pathlib import Path


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _strength_score(row: dict[str, str]) -> float:
    sample_factor = min(1.0, _to_int(row.get("drawn", "0")) / 6.0)
    played_factor = min(1.0, _to_int(row.get("played", "0")) / 4.0)
    return sample_factor * (
        0.45 * _to_float(row.get("win_rate_when_played", "0"))
        + 0.30 * _to_float(row.get("win_rate_when_drawn", "0"))
        + 0.15 * _to_float(row.get("play_rate_when_drawn", "0"))
        + 0.10 * played_factor
    )


def render_card_diagnostics_html(csv_path: Path, html_path: Path) -> None:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        row["strength_score"] = f"{_strength_score(row):.4f}"

    deck_names = sorted({row["deck_name"] for row in rows})
    grouped: dict[str, list[dict[str, str]]] = {deck_name: [] for deck_name in deck_names}
    for row in rows:
        grouped[row["deck_name"]].append(row)

    summary_cards: list[str] = []
    deck_sections: list[str] = []
    table_rows: list[dict[str, str]] = []

    for deck_name in deck_names:
        deck_rows = grouped[deck_name]
        sorted_rows = sorted(deck_rows, key=lambda row: float(row["strength_score"]), reverse=True)
        strongest = sorted_rows[:5]
        weakest = list(reversed(sorted_rows[-5:]))
        avg_played = sum(_to_int(row["played"]) for row in deck_rows) / max(len(deck_rows), 1)
        avg_strength = sum(float(row["strength_score"]) for row in deck_rows) / max(len(deck_rows), 1)

        summary_cards.append(
            f"""
            <section class=\"summary-card\">
              <h3>{html.escape(deck_name)}</h3>
              <p>Cards: {len(deck_rows)}</p>
              <p>Avg strength: {avg_strength:.3f}</p>
              <p>Avg plays tracked: {avg_played:.2f}</p>
            </section>
            """
        )

        def _render_rank_list(rank_rows: list[dict[str, str]], cls: str) -> str:
            items = []
            for row in rank_rows:
                items.append(
                    f"<li><span>{html.escape(row['card_name'])}</span><strong>{float(row['strength_score']):.3f}</strong></li>"
                )
            return f"<ol class=\"rank-list {cls}\">{''.join(items)}</ol>"

        deck_sections.append(
            f"""
            <section class=\"deck-panel\">
              <div class=\"deck-header\">
                <h2>{html.escape(deck_name)}</h2>
                <p>Top and bottom cards by a simple stats-only strength proxy from trained play.</p>
              </div>
              <div class=\"rank-grid\">
                <div>
                  <h4>Likely Strongest</h4>
                  {_render_rank_list(strongest, 'strong')}
                </div>
                <div>
                  <h4>Likely Weakest</h4>
                  {_render_rank_list(weakest, 'weak')}
                </div>
              </div>
            </section>
            """
        )

        table_rows.extend(sorted_rows)

    table_rows_json = json.dumps(table_rows)
    deck_names_json = json.dumps(deck_names)

    html_text = f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>AI Card Diagnostics</title>
  <style>
    :root {{
      --bg: #f4efe7;
      --panel: #fffaf2;
      --ink: #231b16;
      --muted: #6f6256;
      --line: #d9cbb9;
      --accent: #0c7c59;
      --accent-soft: #dff3ec;
      --warning: #b8542f;
      --warning-soft: #f7e2d8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, 'Times New Roman', serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #fff7e6 0, transparent 30%),
        linear-gradient(180deg, #f7f0e6 0%, var(--bg) 100%);
    }}
    main {{ max-width: 1320px; margin: 0 auto; padding: 32px 24px 48px; }}
    h1, h2, h3, h4 {{ margin: 0; font-weight: 700; }}
    p {{ margin: 0; color: var(--muted); }}
    .hero {{ display: grid; gap: 14px; margin-bottom: 28px; }}
    .hero h1 {{ font-size: clamp(2rem, 4vw, 3.6rem); letter-spacing: -0.04em; }}
    .hero p {{ max-width: 70ch; line-height: 1.5; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 28px; }}
    .summary-card, .deck-panel, .controls, .table-panel {{
      background: color-mix(in srgb, var(--panel) 94%, white 6%);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 10px 30px rgba(35, 27, 22, 0.06);
    }}
    .summary-card {{ padding: 18px; display: grid; gap: 8px; }}
    .deck-list {{ display: grid; gap: 18px; margin-bottom: 28px; }}
    .deck-panel {{ padding: 20px; display: grid; gap: 16px; }}
    .deck-header {{ display: grid; gap: 6px; }}
    .rank-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 18px; }}
    .rank-list {{ margin: 0; padding-left: 20px; display: grid; gap: 10px; }}
    .rank-list li {{ display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; border-radius: 12px; }}
    .rank-list.strong li {{ background: var(--accent-soft); }}
    .rank-list.weak li {{ background: var(--warning-soft); }}
    .controls {{ padding: 16px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 18px; }}
    .controls input, .controls select {{
      border: 1px solid var(--line);
      background: white;
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
      color: inherit;
    }}
    .table-panel {{ overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.96rem; }}
    thead {{ background: #efe2d1; }}
    th, td {{ padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; }}
    th {{ cursor: pointer; user-select: none; position: sticky; top: 0; z-index: 1; }}
    tbody tr:hover {{ background: #fff2dc; }}
    .metric {{ font-variant-numeric: tabular-nums; }}
    .score-bar {{ width: 120px; height: 10px; border-radius: 999px; background: #eadbc7; overflow: hidden; }}
    .score-bar > span {{ display: block; height: 100%; background: linear-gradient(90deg, #c75c36 0%, #d8a041 35%, #0c7c59 100%); }}
    @media (max-width: 900px) {{
      table {{ display: block; overflow-x: auto; white-space: nowrap; }}
      .controls {{ align-items: stretch; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class=\"hero\">
      <h1>AI Card Diagnostics</h1>
      <p>This report turns the trained-play CSV into a deck-by-deck view of likely strong and weak cards. The current signal is stats-only: cost, power, draw frequency, play frequency, and win correlation from vanilla-card self-play.</p>
    </section>

    <section class=\"summary-grid\">{''.join(summary_cards)}</section>

    <section class=\"deck-list\">{''.join(deck_sections)}</section>

    <section class=\"controls\">
      <input id=\"search\" type=\"search\" placeholder=\"Filter by card name\">
      <select id=\"deck-filter\">
        <option value=\"\">All decks</option>
      </select>
      <span id=\"row-count\"></span>
    </section>

    <section class=\"table-panel\">
      <table>
        <thead>
          <tr>
            <th data-key=\"deck_name\">Deck</th>
            <th data-key=\"card_name\">Card</th>
            <th data-key=\"cost\">Cost</th>
            <th data-key=\"power\">Power</th>
            <th data-key=\"drawn\">Drawn</th>
            <th data-key=\"played\">Played</th>
            <th data-key=\"play_rate_when_drawn\">Play Rate</th>
            <th data-key=\"win_rate_when_drawn\">Win When Drawn</th>
            <th data-key=\"win_rate_when_played\">Win When Played</th>
            <th data-key=\"avg_play_turn\">Avg Play Turn</th>
            <th data-key=\"strength_score\">Strength Score</th>
          </tr>
        </thead>
        <tbody id=\"table-body\"></tbody>
      </table>
    </section>
  </main>

  <script>
    const rows = {table_rows_json};
    const deckNames = {deck_names_json};
    const numericKeys = new Set([
      'cost', 'power', 'drawn', 'played', 'play_rate_when_drawn',
      'win_rate_when_drawn', 'win_rate_when_played', 'avg_play_turn', 'strength_score'
    ]);
    const deckFilter = document.getElementById('deck-filter');
    const searchInput = document.getElementById('search');
    const tableBody = document.getElementById('table-body');
    const rowCount = document.getElementById('row-count');
    let sortKey = 'strength_score';
    let sortDir = 'desc';

    for (const deck of deckNames) {{
      const option = document.createElement('option');
      option.value = deck;
      option.textContent = deck;
      deckFilter.appendChild(option);
    }}

    function compareRows(a, b) {{
      const aVal = a[sortKey];
      const bVal = b[sortKey];
      const direction = sortDir === 'asc' ? 1 : -1;
      if (numericKeys.has(sortKey)) {{
        return (parseFloat(aVal) - parseFloat(bVal)) * direction;
      }}
      return String(aVal).localeCompare(String(bVal)) * direction;
    }}

    function render() {{
      const search = searchInput.value.trim().toLowerCase();
      const deck = deckFilter.value;
      let filtered = rows.filter((row) => {{
        const matchesDeck = !deck || row.deck_name === deck;
        const matchesSearch = !search || row.card_name.toLowerCase().includes(search);
        return matchesDeck && matchesSearch;
      }});

      filtered = filtered.sort(compareRows);
      rowCount.textContent = `${{filtered.length}} cards shown`;
      tableBody.innerHTML = '';

      for (const row of filtered) {{
        const tr = document.createElement('tr');
        const scorePct = Math.max(0, Math.min(100, Math.round(parseFloat(row.strength_score) * 100)));
        tr.innerHTML = `
          <td>${{row.deck_name}}</td>
          <td>${{row.card_name}}</td>
          <td class=\"metric\">${{row.cost}}</td>
          <td class=\"metric\">${{row.power}}</td>
          <td class=\"metric\">${{row.drawn}}</td>
          <td class=\"metric\">${{row.played}}</td>
          <td class=\"metric\">${{row.play_rate_when_drawn}}</td>
          <td class=\"metric\">${{row.win_rate_when_drawn}}</td>
          <td class=\"metric\">${{row.win_rate_when_played}}</td>
          <td class=\"metric\">${{row.avg_play_turn}}</td>
          <td>
            <div class=\"score-bar\"><span style=\"width:${{scorePct}}%\"></span></div>
            <div class=\"metric\">${{row.strength_score}}</div>
          </td>
        `;
        tableBody.appendChild(tr);
      }}
    }}

    document.querySelectorAll('th[data-key]').forEach((th) => {{
      th.addEventListener('click', () => {{
        const nextKey = th.dataset.key;
        if (sortKey === nextKey) {{
          sortDir = sortDir === 'asc' ? 'desc' : 'asc';
        }} else {{
          sortKey = nextKey;
          sortDir = numericKeys.has(sortKey) ? 'desc' : 'asc';
        }}
        render();
      }});
    }});
    searchInput.addEventListener('input', render);
    deckFilter.addEventListener('change', render);
    render();
  </script>
</body>
</html>
    """

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")