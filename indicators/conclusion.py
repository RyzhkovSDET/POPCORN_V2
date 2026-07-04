"""
Заключение по монете: два коротких читаемых текста + таблица рекомендаций
на 5 горизонтов (1ч/4ч/12ч/1д/2д).

Текст №1 (generate_conclusion_text) -- описание ТЕКУЩЕГО состояния монеты
на основе метрик, которые уже есть в приложении (Score, RSI, паттерн,
funding, OI, объём, пробой). Ничего нового не считает, просто расшифровывает
цифры таблицы словами.

Текст №2 (generate_logic_text) -- "Моя логика прогноза": объясняет ИТОГОВУЮ
рекомендацию (LONG/SHRT/NEUT) и уровни входа/стопа, дополнительно
опираясь на Order Book консенсус нескольких бирж через CoinAPI (см.
api.coinapi_data.fetch_orderbook_consensus), если ключ настроен.

Вероятности и рекомендации по горизонтам считаются одной и той же
взвешенной моделью (compute_probabilities) -- вес каждого сигнала (Score,
Прогноз, RSI, паттерн, funding, пробой) разный для короткого и длинного
горизонта: короткий (1ч) больше опирается на сиюминутный momentum,
длинный (2д) -- на прогноз и устойчивость структуры пробоя.

Вероятности сознательно ограничены диапазоном [PROB_FLOOR, PROB_CEILING]
(15-85%), а не 0-100% -- даже при полностью согласованных сигналах модель
не должна выглядеть избыточно уверенной. Это эвристика на основе текущих
технических метрик, НЕ финансовая рекомендация и не гарантия исхода.
"""
from typing import Optional

PROB_FLOOR = 15
PROB_CEILING = 85

# Порог вероятности, при котором даём направленную рекомендацию, а не
# "NEUT". 60/40 -- сознательно не 50/50: небольшой перевес в сторону
# одного направления ещё не повод рекомендовать сделку, шум может дать
# 52% в любую сторону.
BUY_THRESHOLD = 60
SHORT_THRESHOLD = 40


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Отдельные сигналы, каждый нормализован в диапазон [-1, +1]
# (-1 = максимально медвежий, +1 = максимально бычий)
# ---------------------------------------------------------------------------

def _rsi_signal(rsi: Optional[float]) -> float:
    """Контрарный сигнал по RSI: перепроданность (<30) -- бычий, перекупленность (>70) -- медвежий."""
    if rsi is None:
        return 0.0
    if rsi < 30:
        return _clip((30 - rsi) / 30, 0, 1)
    if rsi > 70:
        return -_clip((rsi - 70) / 30, 0, 1)
    return 0.0


def _pattern_signal(pattern_bias: Optional[str]) -> float:
    return {"bull": 1.0, "bear": -1.0}.get(pattern_bias, 0.0)


def _funding_signal(funding_rate: Optional[float]) -> float:
    """Контрарный сигнал: высокое положительное финансирование -- лонги переплачивают шортам, риск коррекции вниз, и наоборот."""
    if funding_rate is None:
        return 0.0
    return -_clip(funding_rate / 0.05, -1, 1)  # ставка 0.05%+ уже считается "горячей"


def _breakout_signal(break_counters: Optional[dict]) -> float:
    """Серия пробоев максимумов -- бычье продолжение тренда, минимумов -- медвежье."""
    if not break_counters:
        return 0.0
    max_count = break_counters.get("max_count") or 0
    min_count = break_counters.get("min_count") or 0
    if max_count:
        return _clip(max_count / 3, 0, 1)
    if min_count:
        return -_clip(min_count / 3, 0, 1)
    return 0.0


def _liquidation_signal(liquidations: Optional[dict]) -> float:
    """
    Контрарный сигнал (см. api.liquidations_aggregator.fetch_liquidation_consensus):
    волна ликвидаций ЛОНГОВ (принудительные продажи на падении) часто
    означает, что "слабые руки" уже выбиты из рынка -- потенциал для
    отскока вверх. Волна ликвидаций ШОРТОВ -- шорт-сквиз, потенциал для
    отката вниз после исчерпания импульса. Эффект сильнее всего сразу
    после самой волны и быстро выдыхается -- поэтому вес этого сигнала
    выше на коротких горизонтах и ниже на длинных (см. _HORIZON_WEIGHTS).
    """
    if not liquidations:
        return 0.0
    return _clip(liquidations.get("liquidation_bias", 0.0), -1, 1)


def _confidence_multiplier(metrics: dict) -> float:
    """
    Растущий объём и растущий открытый интерес означают, что за движением
    стоит реальный приток денег -- усиливает уверенность в текущем сигнале
    В ЛЮБУЮ СТОРОНУ (объём сам по себе не задаёт направление, только
    подтверждает или ослабляет то, что уже показывают остальные сигналы).
    Множитель ограничен [0.7, 1.3], чтобы объём не мог сам по себе
    развернуть итоговый вывод, только усилить или приглушить его.
    """
    signals = []
    volume_pct = metrics.get("volume_pct") or {}
    for key in ("1ч", "3ч", "6ч", "24ч"):
        v = volume_pct.get(key)
        if v is not None:
            signals.append(v)
    oi_pct = metrics.get("oi_pct_change")
    if oi_pct is not None:
        signals.append(oi_pct)
    if not signals:
        return 1.0
    avg = sum(signals) / len(signals)
    return _clip(1.0 + avg / 100, 0.7, 1.3)


# Веса компонентов по горизонтам. Короткие горизонты (1ч) больше опираются
# на сиюминутный momentum (Score/RSI/паттерн), длинные (2д) -- на прогноз
# и устойчивость пробоя структуры, а не на momentum, который за пару дней
# может смениться уже несколько раз. Веса не обязаны суммироваться ровно
# в 1.0 -- итоговое значение всё равно ограничивается диапазоном [-1, 1]
# перед переводом в проценты (см. compute_probabilities).
_HORIZON_WEIGHTS = {
    "1ч":  {"score": 0.28, "forecast": 0.28, "rsi": 0.14, "pattern": 0.14, "funding": 0.04, "breakout": 0.04, "liquidation": 0.08},
    "4ч":  {"score": 0.22, "forecast": 0.30, "rsi": 0.10, "pattern": 0.09, "funding": 0.07, "breakout": 0.12, "liquidation": 0.10},
    "12ч": {"score": 0.16, "forecast": 0.32, "rsi": 0.07, "pattern": 0.05, "funding": 0.09, "breakout": 0.21, "liquidation": 0.08},
    "1д":  {"score": 0.11, "forecast": 0.32, "rsi": 0.04, "pattern": 0.03, "funding": 0.09, "breakout": 0.23, "liquidation": 0.06},
    "2д":  {"score": 0.07, "forecast": 0.30, "rsi": 0.03, "pattern": 0.02, "funding": 0.09, "breakout": 0.28, "liquidation": 0.04},
}

HORIZONS = ("1ч", "4ч", "12ч", "1д", "2д")


def compute_probabilities(metrics: dict, forecasts: dict, liquidations: Optional[dict] = None) -> dict:
    """
    metrics -- сырые метрики монеты (score, rsi, pattern_bias, funding_rate,
    oi_pct_change, volume_pct, break_counters -- см. ui.watchlist._fetch_ticker_row).
    forecasts -- {"1ч": score|None, "4ч": score|None, "12ч": .., "1д": .., "2д": ..},
    прогноз (0-100) на КАЖДЫЙ горизонт отдельно (считается вызывающим кодом
    через ui.metrics.get_forecast_score с разным interval/limit на горизонт).
    liquidations -- необязательный консенсус ликвидаций (см.
    api.liquidations_aggregator.fetch_liquidation_consensus). Если недоступен
    (нет ни одного источника) -- компонент просто не влияет (сигнал 0).

    Возвращает {"1ч": {"up": int, "down": int}, ...} -- вероятности в
    процентах (up + down == 100).
    """
    score = metrics.get("score")
    score_dev = ((score - 50) / 50) if score is not None else 0.0

    rsi_sig = _rsi_signal(metrics.get("rsi"))
    pattern_sig = _pattern_signal(metrics.get("pattern_bias"))
    funding_sig = _funding_signal(metrics.get("funding_rate"))
    breakout_sig = _breakout_signal(metrics.get("break_counters"))
    liquidation_sig = _liquidation_signal(liquidations)
    confidence = _confidence_multiplier(metrics)

    result = {}
    for horizon in HORIZONS:
        weights = _HORIZON_WEIGHTS[horizon]
        forecast_score = forecasts.get(horizon)
        # Нет прогноза (не хватило истории на этом таймфрейме) -- откатываемся
        # на текущий Score вместо того, чтобы обнулять компонент прогноза.
        forecast_dev = ((forecast_score - 50) / 50) if forecast_score is not None else score_dev

        raw = (
            weights["score"] * score_dev
            + weights["forecast"] * forecast_dev
            + weights["rsi"] * rsi_sig
            + weights["pattern"] * pattern_sig
            + weights["funding"] * funding_sig
            + weights["breakout"] * breakout_sig
            + weights["liquidation"] * liquidation_sig
        )
        biased = _clip(raw * confidence, -1, 1)
        prob_up = 50 + biased * (PROB_CEILING - 50)
        prob_up = _clip(prob_up, PROB_FLOOR, PROB_CEILING)
        result[horizon] = {"up": round(prob_up), "down": round(100 - prob_up)}
    return result


# ---------------------------------------------------------------------------
# Уровни входа/стопа -- комбинация структурного уровня (свинг-хай/лоу по
# дневным свечам) и Order Book консенсуса (если доступен CoinAPI-ключ)
# ---------------------------------------------------------------------------

def compute_entry_levels(price: Optional[float], atr: Optional[float],
                          structural_support: Optional[float], structural_resistance: Optional[float],
                          orderbook: Optional[dict]) -> dict:
    """
    Комбинирует структурный уровень (см. indicators.analysis.find_support_resistance
    -- свинг-хай/лоу по дневным свечам, "исторический" уровень) с Order Book
    консенсусом нескольких бирж (см. api.coinapi_data.fetch_orderbook_consensus
    -- более "живой", сиюминутный уровень), если он доступен.

    Если доступны оба -- усредняет. Если доступен только один -- берёт его
    как есть. Если нет ни одного -- возвращает None для соответствующего
    уровня (вызывающий код должен показать "н/д", а не выдумывать число).

    stop_buffer -- ATR, если он известен, иначе запасной вариант 1% от
    цены -- отступ ЗА уровнем, чтобы стоп не стоял ровно на самом уровне
    (там чаще всего и происходит "охота за стопами").
    """
    stop_buffer = atr if atr else (price * 0.01 if price else None)

    support_candidates = [v for v in (structural_support, orderbook.get("support_price") if orderbook else None) if v is not None]
    resistance_candidates = [v for v in (structural_resistance, orderbook.get("resistance_price") if orderbook else None) if v is not None]

    support = sum(support_candidates) / len(support_candidates) if support_candidates else None
    resistance = sum(resistance_candidates) / len(resistance_candidates) if resistance_candidates else None

    return {
        "support": support,
        "resistance": resistance,
        "support_stop": (support - stop_buffer) if (support is not None and stop_buffer is not None) else None,
        "resistance_stop": (resistance + stop_buffer) if (resistance is not None and stop_buffer is not None) else None,
    }


def build_recommendations(probabilities: dict, entry_levels: dict) -> dict:
    """
    Переводит вероятности каждого горизонта в рекомендацию LONG/SHRT/NEUT +
    цену входа и стопа. Цены входа/стопа ОДНИ И ТЕ ЖЕ для всех горизонтов
    (уровень поддержки/сопротивления не зависит от горизонта прогноза) --
    разной по горизонтам является только сама рекомендация.

    Обозначения намеренно короткие (4 буквы, латиница) -- компактно
    помещаются в одну строку рядом с ценой входа/стопа:
    LONG -- рынок склоняется вверх (открыть лонг),
    SHRT -- рынок склоняется вниз (открыть шорт),
    NEUT -- сигналы смешанные, направленной сделки не видно.
    """
    result = {}
    for horizon in HORIZONS:
        p = probabilities[horizon]
        if p["up"] >= BUY_THRESHOLD:
            rec, entry, stop = "LONG", entry_levels.get("support"), entry_levels.get("support_stop")
        elif p["up"] <= SHORT_THRESHOLD:
            rec, entry, stop = "SHRT", entry_levels.get("resistance"), entry_levels.get("resistance_stop")
        else:
            rec, entry, stop = "NEUT", None, None
        result[horizon] = {"rec": rec, "entry": entry, "stop": stop, "up": p["up"], "down": p["down"]}
    return result


# ---------------------------------------------------------------------------
# Текст №1 -- текущее состояние монеты (без изменений в логике)
# ---------------------------------------------------------------------------

def generate_conclusion_text(metrics: dict, ticker: str) -> str:
    """Короткое (обычно 3-6 предложений) читаемое описание текущего состояния монеты."""
    if not metrics:
        return "Недостаточно данных для заключения -- кликни на монету заново."

    coin = ticker.replace("USDT", "")
    score = metrics.get("score")
    rsi = metrics.get("rsi")
    pattern_bias = metrics.get("pattern_bias")
    funding = metrics.get("funding_rate")
    oi_pct = metrics.get("oi_pct_change")
    break_counters = metrics.get("break_counters") or {}
    volume_pct = metrics.get("volume_pct") or {}

    sentences = []

    if score is not None:
        if score >= 70:
            sentences.append(
                f"{coin} сейчас в зоне покупки (Score {score}) -- большинство технических "
                f"сигналов (RSI, EMA, MACD) складываются в бычью сторону."
            )
        elif score <= 30:
            sentences.append(
                f"{coin} сейчас в зоне продажи (Score {score}) -- технические сигналы "
                f"указывают на медвежье давление."
            )
        else:
            sentences.append(
                f"{coin} сейчас в нейтральной зоне (Score {score}) -- явного перевеса "
                f"ни у покупателей, ни у продавцов нет."
            )

    if rsi is not None:
        if rsi < 30:
            sentences.append(f"RSI на уровне {rsi:.0f} говорит о перепроданности -- возможен отскок вверх.")
        elif rsi > 70:
            sentences.append(f"RSI на уровне {rsi:.0f} говорит о перекупленности -- риск коррекции вниз.")

    if pattern_bias == "bull":
        sentences.append("Последняя свечная структура тоже бычья, что подтверждает сигнал.")
    elif pattern_bias == "bear":
        sentences.append("Последняя свечная структура медвежья, что усиливает риск снижения.")

    vol_24h = volume_pct.get("24ч")
    if vol_24h is not None and abs(vol_24h) > 10:
        direction = "растёт" if vol_24h > 0 else "падает"
        trust = "усиливает доверие к движению" if vol_24h > 0 else "указывает на угасающий интерес"
        sentences.append(f"Объём торгов за 24ч заметно {direction} ({vol_24h:+.0f}%), что {trust}.")

    if oi_pct is not None and abs(oi_pct) > 5:
        direction = "растёт" if oi_pct > 0 else "снижается"
        money = "заходят новые деньги" if oi_pct > 0 else "закрываются позиции"
        sentences.append(f"Открытый интерес по фьючерсам {direction} ({oi_pct:+.1f}%) -- в рынок {money}.")

    if funding is not None and abs(funding) > 0.03:
        if funding > 0:
            sentences.append(
                f"Funding rate повышен ({funding:+.3f}%) -- лонги переплачивают шортам, "
                f"рынок может быть перегрет в сторону роста."
            )
        else:
            sentences.append(
                f"Funding rate отрицательный ({funding:+.3f}%) -- шорты переплачивают "
                f"лонгам, возможен сквиз вверх."
            )

    if break_counters.get("max_count"):
        sentences.append(
            f"Цена пробивает локальные максимумы {break_counters['max_count']} раз(а) "
            f"подряд -- восходящая структура сохраняется."
        )
    elif break_counters.get("min_count"):
        sentences.append(
            f"Цена пробивает локальные минимумы {break_counters['min_count']} раз(а) "
            f"подряд -- нисходящая структура сохраняется."
        )

    if not sentences:
        sentences.append(f"Данных по {coin} пока недостаточно для содержательного заключения.")

    return " ".join(sentences)


# ---------------------------------------------------------------------------
# Текст №2 -- "Моя логика прогноза" (Order Book + уровни + итоговая сводка)
# ---------------------------------------------------------------------------

def generate_logic_text(orderbook: Optional[dict], liquidations: Optional[dict],
                         entry_levels: dict, recommendations: dict) -> str:
    """
    Короткое (2-6 предложений) объяснение итоговой рекомендации: что
    показывает Order Book консенсус, что показывают ликвидации, где
    ориентировочные уровни входа, и куда склоняется большинство горизонтов.
    """
    sentences = []

    if orderbook:
        imbalance = orderbook.get("bid_ask_imbalance", 0.0)
        n_ex = orderbook.get("exchanges_used", 0)
        if imbalance > 0.1:
            sentences.append(f"Order Book консенсус ({n_ex} бирж(и) через CoinAPI) показывает перевес покупателей в стакане ({imbalance:+.0%}).")
        elif imbalance < -0.1:
            sentences.append(f"Order Book консенсус ({n_ex} бирж(и) через CoinAPI) показывает перевес продавцов в стакане ({imbalance:+.0%}).")
        else:
            sentences.append(f"Order Book консенсус ({n_ex} бирж(и) через CoinAPI) сбалансирован -- явного перевеса ни у покупателей, ни у продавцов нет.")
    else:
        sentences.append("Order Book данные недоступны (нет CoinAPI-ключа или дневной лимит запросов исчерпан) -- уровни ниже посчитаны только по структуре цены.")

    if liquidations:
        bias = liquidations.get("liquidation_bias", 0.0)
        n_src = liquidations.get("sources_used", 0)
        if bias > 0.15:
            sentences.append(f"За последнее время преобладают ликвидации лонгов ({n_src} источник(а): Binance/OKX) -- слабые руки уже выбиты, это контрарно-бычий сигнал.")
        elif bias < -0.15:
            sentences.append(f"За последнее время преобладают ликвидации шортов ({n_src} источник(а): Binance/OKX) -- похоже на шорт-сквиз, контрарно-медвежий сигнал на продолжение.")
    else:
        sentences.append("Данные по ликвидациям сейчас недоступны (поток Binance ещё не накопил историю или OKX не ответил).")

    support = entry_levels.get("support")
    resistance = entry_levels.get("resistance")
    if support is not None:
        sentences.append(f"Ближайший уровень спроса (ориентир для входа в лонг) ~{support:,.4f}.")
    if resistance is not None:
        sentences.append(f"Ближайший уровень предложения (ориентир для входа в шорт) ~{resistance:,.4f}.")

    buy_count = sum(1 for r in recommendations.values() if r["rec"] == "LONG")
    short_count = sum(1 for r in recommendations.values() if r["rec"] == "SHRT")
    total = len(recommendations)
    if buy_count > short_count:
        sentences.append(f"Большинство горизонтов ({buy_count} из {total}) сейчас склоняются к покупке.")
    elif short_count > buy_count:
        sentences.append(f"Большинство горизонтов ({short_count} из {total}) сейчас склоняются к шорту.")
    else:
        sentences.append("Горизонты дают смешанные сигналы -- явного консенсуса по направлению нет.")

    sentences.append("Это эвристика на основе текущих данных, не финансовая рекомендация -- решение и риск всегда на тебе.")
    return " ".join(sentences)


# ---------------------------------------------------------------------------
# Единая точка входа для UI
# ---------------------------------------------------------------------------

def build_full_conclusion(
    metrics: dict,
    forecasts: dict,
    ticker: str,
    entry_levels: dict,
    orderbook: Optional[dict] = None,
    liquidations: Optional[dict] = None,
    coinapi_overview: Optional[dict] = None,
) -> dict:
    """
    Собирает всё для UI в один словарь:
    {"text": str, "logic_text": str, "probabilities": {...}, "recommendations": {...}}

    coinapi_overview -- необязательные доп. данные с CoinAPI (см.
    api.coinapi_data.fetch_asset_overview) -- независимая от Binance/
    CoinGecko цена для сверки, дописывается в конец текста №1, если доступна.
    """
    text = generate_conclusion_text(metrics, ticker)

    if coinapi_overview and coinapi_overview.get("price_usd") and metrics.get("price"):
        own_price = metrics["price"]
        capi_price = coinapi_overview["price_usd"]
        if own_price:
            diff_pct = abs(capi_price - own_price) / own_price * 100
            if diff_pct < 1:
                text += " Цена подтверждена независимым источником (CoinAPI, усреднено по многим биржам) -- расхождение минимально."
            else:
                text += f" Внимание: цена по CoinAPI отличается на {diff_pct:.1f}% от используемого источника -- возможна рассинхронизация между биржами."

    probabilities = compute_probabilities(metrics, forecasts, liquidations)
    recommendations = build_recommendations(probabilities, entry_levels)
    logic_text = generate_logic_text(orderbook, liquidations, entry_levels, recommendations)

    return {
        "text": text,
        "logic_text": logic_text,
        "probabilities": probabilities,
        "recommendations": recommendations,
    }