"""
Алерты по пороговым зонам Score / Прогноза.

Это не полноценная система уведомлений вне браузера (Telegram/email/push) --
такая штука требует отдельного постоянно работающего процесса и вынесена
за рамки этой итерации. Здесь -- то, что можно сделать чисто внутри
Streamlit-приложения, пока оно открыто:

1. Детект ПЕРЕХОДА метрики (Score и/или Прогноз) в экстремальную зону
   ПОК/ПРД с прошлого цикла обновления watchlist на этот.
2. Всплывающее уведомление st.toast() в момент перехода.
3. Журнал последних срабатываний за сессию -- toast сам по себе исчезает
   через несколько секунд, а тут можно посмотреть, что произошло, пока
   не смотрел на экран.

Сознательно алертим только НА ПЕРЕХОД, а не на каждый цикл, пока метрика
УЖЕ стоит в зоне ПОК/ПРД -- иначе монета, зависшая в зоне продажи на час,
заспамила бы и toast, и журнал одним и тем же сообщением каждые
REFRESH_SEC секунд.

Сознательно алертим только на вход в САМУЮ СИЛЬНУЮ зону (зелёная/красная),
а не на любое изменение зоны -- средние зоны (ППК/НЕЙТ/ППД) слишком часто
мигают туда-обратно на шуме и завалили бы журнал бесполезными
срабатываниями.

Ограничение: и "память" о предыдущей зоне, и журнал живут в
st.session_state -- переживают обычный rerun (клик по кнопке, переключение
селектора), но сбрасываются при полном перезапуске streamlit-процесса.
Персистентность на диск сознательно не добавлена: пришлось бы писать файл
на каждый цикл REFRESH_SEC ради истории, которая нужна раз в день
посмотреть -- неоправданный I/O.
"""
from datetime import datetime, timezone

import streamlit as st

from indicators.signal_zones import classify_score

MAX_ALERT_LOG = 50

# Зоны, переход В которые считается достаточно значимым, чтобы алертить.
# classify_score() возвращает 5 зон (green/yellow/white/blue/red) -- сюда
# попадают только крайние (сильная покупка / сильная продажа).
_ALERT_ZONES = {"green": ("🟢", "ПОК"), "red": ("🔴", "ПРД")}

_METRIC_LABELS = {"score": "Сигнал", "forecast_score": "Прогноз"}


def _init_alerts_state():
    if "alerts_enabled" not in st.session_state:
        st.session_state.alerts_enabled = True
    if "alerts_watch_score" not in st.session_state:
        st.session_state.alerts_watch_score = True
    if "alerts_watch_forecast" not in st.session_state:
        st.session_state.alerts_watch_forecast = False
    if "alerts_prev_zone" not in st.session_state:
        # (ticker, metric) -> последняя увиденная зона. Нужно, чтобы
        # алертить именно на ПЕРЕХОД между зонами, а не на каждый рендер,
        # пока метрика продолжает стоять в той же зоне.
        st.session_state.alerts_prev_zone = {}
    if "alerts_log" not in st.session_state:
        st.session_state.alerts_log = []  # новые записи вставляются в начало


def render_alerts_toggle():
    """Компактная строка настроек алертов -- показывается над watchlist,
    рядом с остальными селекторами (не внутри автообновляемого фрагмента
    таблицы, чтобы не переинициализировать чекбоксы каждый REFRESH_SEC)."""
    _init_alerts_state()
    c1, c2, c3 = st.columns([1, 1, 1])
    st.session_state.alerts_enabled = c1.checkbox(
        "🔔 Алерты вкл.",
        value=st.session_state.alerts_enabled,
        help="Всплывающее уведомление, когда Сигнал/Прогноз монеты входит в зону ПОК или ПРД.",
    )
    st.session_state.alerts_watch_score = c2.checkbox(
        "Сигнал (Score)",
        value=st.session_state.alerts_watch_score,
        disabled=not st.session_state.alerts_enabled,
    )
    st.session_state.alerts_watch_forecast = c3.checkbox(
        "Прогноз",
        value=st.session_state.alerts_watch_forecast,
        disabled=not st.session_state.alerts_enabled,
    )


def _check_metric(ticker: str, metric_name: str, value) -> None:
    if value is None:
        return

    zone, _label, _arrow = classify_score(value)
    key = (ticker, metric_name)
    prev_zone = st.session_state.alerts_prev_zone.get(key)
    st.session_state.alerts_prev_zone[key] = zone

    if prev_zone is None:
        return  # первый раз видим эту пару (ticker, metric) -- не алертим на "переход из ниоткуда"
    if prev_zone == zone:
        return  # зона не изменилась с прошлого цикла
    if zone not in _ALERT_ZONES:
        return  # перешли в среднюю зону (ППК/НЕЙТ/ППД) -- не считается значимым событием

    icon, zone_label = _ALERT_ZONES[zone]
    coin_name = ticker.replace("USDT", "")
    metric_label = _METRIC_LABELS.get(metric_name, metric_name)

    st.toast(f"{icon} {coin_name}: {metric_label} вошёл в зону {zone_label} ({round(value)})")
    st.session_state.alerts_log.insert(0, {
        "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "ticker": coin_name,
        "metric": metric_label,
        "zone": zone_label,
        "value": round(value),
        "icon": icon,
    })
    st.session_state.alerts_log = st.session_state.alerts_log[:MAX_ALERT_LOG]


def check_and_fire_alerts(raw_by_ticker: dict) -> None:
    """
    Вызывается на каждом цикле обновления watchlist (см. ui/watchlist.py,
    внутри st.fragment) -- сравнивает текущую зону Score/Прогноза каждой
    монеты с прошлым циклом и алертит на вход в ПОК/ПРД.

    raw_by_ticker -- тот же словарь сырых метрик, что уже собран для
    таблицы: ticker -> {"score": .., "forecast_score": .., ...}.
    """
    _init_alerts_state()
    if not st.session_state.alerts_enabled:
        return
    for ticker, raw in raw_by_ticker.items():
        if st.session_state.alerts_watch_score:
            _check_metric(ticker, "score", raw.get("score"))
        if st.session_state.alerts_watch_forecast:
            _check_metric(ticker, "forecast_score", raw.get("forecast_score"))


def render_alerts_log() -> None:
    """Разворачиваемый журнал последних срабатываний за сессию -- сам
    st.toast() исчезает через несколько секунд, здесь можно посмотреть
    историю, если пропустил момент."""
    _init_alerts_state()
    log = st.session_state.alerts_log
    with st.expander(f"🔔 Журнал алертов ({len(log)})", expanded=False):
        if not log:
            st.caption("Пока не было срабатываний в этой сессии.")
            return
        for entry in log:
            st.markdown(
                f"`{entry['ts']}` {entry['icon']} **{entry['ticker']}** -- "
                f"{entry['metric']} \u2192 {entry['zone']} ({entry['value']})"
            )