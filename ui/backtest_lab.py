"""
Лаборатория бэктеста -- расширяет точечную проверку одной монеты одной
парой порогов (см. ui/analysis_sidebar._render_backtest_section, слайдеры
"Порог входа"/"Порог выхода") до вопроса "работает ли Score-сигнал вообще,
или прибылен только на одной монете, на одном отрезке, с одной случайно
подобранной парой чисел?"

Три функции для трёх разных вопросов:
- render_backtest_lab()               -- grid-search + walk-forward для
  ОДНОЙ выбранной монеты (вызывается из боковой панели анализа).
- render_watchlist_backtest_summary()  -- один и тот же фиксированный порог
  70/30 сразу для ВСЕХ монет watchlist (вызывается с главной страницы).
"""
import pandas as pd
import streamlit as st

from api.get_data import fetch_data_for_ticker
from indicators.backtest import backtest_score_signal, grid_search_thresholds, walk_forward_validation
from ui.config import SLOW_METRICS_INTERVAL

# ~1000 30-минутных свечей -- около 20 дней истории. Общий лимит для
# grid-search/walk-forward/сводки по watchlist, отдельно от
# ANALYSIS_LOOKBACK_DAYS (тот -- для дневных свечей структурного анализа).
BACKTEST_HISTORY_LIMIT = 1000
WALK_FORWARD_FOLDS = 3


def _render_grid_search(bt_df: pd.DataFrame):
    st.markdown(
        "**🔬 Grid-search порогов**",
        help=(
            "Перебирает много комбинаций порогов входа/выхода вместо одной "
            "(как в разделе выше) -- если прибыльна только одна случайно "
            "подобранная пара чисел, а вокруг неё сплошные минусы, это "
            "признак переобучения на конкретный отрезок, а не реального edge."
        ),
    )
    cells = grid_search_thresholds(bt_df)
    if not cells:
        st.caption("Недостаточно сделок ни на одной комбинации порогов для оценки.")
        return

    positive = [c for c in cells if c.result.edge_vs_buy_hold_pct > 0]
    share_positive = len(positive) / len(cells) * 100
    if share_positive >= 60:
        verdict = "🟢 сигнал стабильно обгоняет buy&hold в широком диапазоне порогов"
    elif share_positive >= 35:
        verdict = "🟡 сигнал работает не везде -- обгоняет buy&hold примерно в половине случаев"
    else:
        verdict = "🔴 сигнал скорее шум -- обгоняет buy&hold лишь в единичных комбинациях порогов"
    st.markdown(f"Прибыльных комбинаций: **{len(positive)} из {len(cells)}** ({share_positive:.0f}%) -- {verdict}")

    def _cells_to_df(cell_list):
        return pd.DataFrame([{
            "Вход": c.buy_threshold,
            "Выход": c.sell_threshold,
            "Стратегия": f"{c.result.total_return_pct:+.1f}%",
            "B&H": f"{c.result.buy_hold_return_pct:+.1f}%",
            "Edge": f"{c.result.edge_vs_buy_hold_pct:+.1f}%",
            "Сделок": c.result.num_trades,
            "Win rate": f"{c.result.win_rate_pct:.0f}%" if c.result.win_rate_pct is not None else "н/д",
        } for c in cell_list])

    st.caption("Топ-5 комбинаций порогов:")
    st.dataframe(_cells_to_df(cells[:5]), hide_index=True, use_container_width=True)

    if len(cells) > 5:
        with st.expander("Худшие 5 комбинаций"):
            st.dataframe(_cells_to_df(cells[-5:]), hide_index=True, use_container_width=True)


def _render_walk_forward(bt_df: pd.DataFrame):
    st.markdown(
        "**📆 Walk-forward по периодам**",
        help=(
            f"Делит историю на {WALK_FORWARD_FOLDS} последовательных периода и "
            "тестирует ОДНИ И ТЕ ЖЕ пороги (70/30) на каждом отдельно -- "
            "показывает, работает ли сигнал стабильно в разных рыночных "
            "условиях, или только в одном конкретном периоде, который "
            "случайно попал в общий бэктест."
        ),
    )
    folds = walk_forward_validation(bt_df, n_folds=WALK_FORWARD_FOLDS)
    if not folds:
        st.caption("Недостаточно истории для деления на периоды.")
        return

    rows = []
    wins, valid = 0, 0
    for f in folds:
        if f.result is None:
            rows.append({"Период": f"#{f.fold_index + 1}", "Дата": "н/д", "Стратегия": "н/д", "B&H": "н/д", "Edge": "н/д", "Сделок": "н/д"})
            continue
        valid += 1
        if f.result.edge_vs_buy_hold_pct > 0:
            wins += 1
        period_label = (
            f"{f.start_date.strftime('%d.%m')} - {f.end_date.strftime('%d.%m')}"
            if f.start_date is not None and f.end_date is not None else "н/д"
        )
        rows.append({
            "Период": f"#{f.fold_index + 1}",
            "Дата": period_label,
            "Стратегия": f"{f.result.total_return_pct:+.1f}%",
            "B&H": f"{f.result.buy_hold_return_pct:+.1f}%",
            "Edge": f"{f.result.edge_vs_buy_hold_pct:+.1f}%",
            "Сделок": f.result.num_trades,
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if valid:
        st.caption(f"Обогнал buy&hold в {wins} из {valid} периодов.")


def render_backtest_lab(ticker: str):
    """Вызывается из ui.analysis_sidebar рядом с точечным бэктестом -- даёт
    расширенную (честную) проверку той же Score-стратегии для выбранной монеты."""
    try:
        bt_df = fetch_data_for_ticker(ticker, interval=SLOW_METRICS_INTERVAL, limit=BACKTEST_HISTORY_LIMIT)
    except Exception as e:
        st.caption(f"Не удалось загрузить историю для расширенной проверки: {e}")
        return
    if bt_df is None or bt_df.empty or len(bt_df) < 200:
        st.caption("Недостаточно истории для grid-search/walk-forward (нужно значительно больше 30-минутных свечей).")
        return

    with st.expander("🔬 Расширенная проверка сигнала (grid-search + walk-forward)", expanded=False):
        _render_grid_search(bt_df)
        st.divider()
        _render_walk_forward(bt_df)
        st.caption(
            "⚠️ Всё ещё long-only без проскальзывания и частичных позиций. Цель -- "
            "отличить устойчивый сигнал от переобученного под один отрезок/один порог, "
            "а не заменить полноценную систему валидации стратегий."
        )


@st.cache_data(ttl=600, show_spinner=False)
def _summarize_all_coins(tickers: tuple) -> pd.DataFrame:
    """Бэктест с дефолтными порогами (70/30) для каждого тикера watchlist.
    Кэш 10 минут -- пересчёт по всем монетам на каждый рендер главной
    страницы был бы неоправданно дорог (каждая монета тянет до 1000 свечей)."""
    rows = []
    for ticker in tickers:
        try:
            df = fetch_data_for_ticker(ticker, interval=SLOW_METRICS_INTERVAL, limit=BACKTEST_HISTORY_LIMIT)
        except Exception:
            continue
        result = backtest_score_signal(df)
        if result is None or result.num_trades == 0:
            continue
        rows.append({
            "Монета": ticker.replace("USDT", ""),
            "Сделок": result.num_trades,
            "Стратегия": result.total_return_pct,
            "Buy&Hold": result.buy_hold_return_pct,
            "Edge": result.edge_vs_buy_hold_pct,
            "Win rate": result.win_rate_pct,
        })
    return pd.DataFrame(rows)


def render_watchlist_backtest_summary():
    """
    Сводка по 70/30-бэктесту сразу для ВСЕХ монет текущего watchlist --
    быстрый ответ на вопрос "сигнал вообще работает где-то ещё, кроме
    монеты, которую я сейчас разглядываю в боковой панели, или это частный
    случай". Пороги одинаковые для всех монет (не подгонялись под каждую
    отдельно) -- сравнение на равных условиях.
    """
    coins = tuple(sorted(st.session_state.get("coins", [])))
    if not coins:
        return

    with st.expander(f"📊 Проверка Score-сигнала (70/30) по всем монетам watchlist ({len(coins)})", expanded=False):
        df = _summarize_all_coins(coins)
        if df.empty:
            st.caption("Недостаточно истории ни у одной монеты для оценки.")
            return

        df = df.sort_values("Edge", ascending=False).reset_index(drop=True)
        share_positive = (df["Edge"] > 0).mean() * 100
        st.markdown(
            f"Обгоняет buy&hold: **{int((df['Edge'] > 0).sum())} из {len(df)}** монет ({share_positive:.0f}%)."
        )

        display_df = df.copy()
        display_df["Стратегия"] = display_df["Стратегия"].map(lambda v: f"{v:+.1f}%")
        display_df["Buy&Hold"] = display_df["Buy&Hold"].map(lambda v: f"{v:+.1f}%")
        display_df["Edge"] = display_df["Edge"].map(lambda v: f"{v:+.1f}%")
        display_df["Win rate"] = display_df["Win rate"].map(lambda v: f"{v:.0f}%" if pd.notna(v) else "н/д")
        st.dataframe(display_df, hide_index=True, use_container_width=True)
        st.caption(
            "Пороги фиксированы (70/30) одинаково для всех монет -- сравнение на равных "
            "условиях, а не подогнано под каждую отдельно."
        )