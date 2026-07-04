# POPCORN v2

Мониторинг криптовалют в реальном времени: watchlist с техническими
сигналами (RSI, MACD, EMA-тренд, Funding Rate, Open Interest) и боковая
панель структурного анализа (поддержка/сопротивление, Fibonacci,
объёмный профиль, risk/reward) по клику на монету.

Также есть:
- **Алерты** -- всплывающее уведомление и журнал за сессию, когда Score
  или Прогноз монеты входит в зону сильной покупки/продажи (включается
  переключателем над таблицей).
- **Честная проверка Score-сигнала** -- кроме одной пары порогов
  (боковая панель), есть grid-search по многим порогам, walk-forward по
  нескольким периодам истории и сводка по всему watchlist сразу с одними
  и теми же порогами -- чтобы отличить реальный edge от переобучения на
  один отрезок/одну монету/один порог.

## Запуск

```bash
pip install -r requirements.txt
streamlit run main.py
```

Откроется на `http://localhost:8501`. Список монет хранится в
`data/coins.json`, создаётся автоматически при первом запуске
(по умолчанию BTCUSDT, ETHUSDT, SOLUSDT).

Приватный API-ключ Binance не требуется — используются только публичные
эндпоинты (klines, funding rate, open interest).

## Структура

```
main.py                     # точка входа: порядок вызова блоков страницы
api/get_data.py             # свечи (Binance -> Coinbase -> Kraken, авто-fallback)
api/futures_data.py         # funding rate + open interest (Binance Futures -> OKX)
api/coingecko_data.py       # общий объём по всем биржам (CoinGecko)
api/screener.py             # пул монет для мини-скринера (CoinGecko markets)
api/ws_stream.py            # живой WebSocket-поток цены (Binance), fallback на REST
storage/coins_storage.py    # список монет (JSON-файл)
indicators/scoring.py       # ЕДИНАЯ формула Score -- используется и в watchlist, и в бэктесте
indicators/signal_zones.py  # 5-зонная палитра сигналов (ПОК/ППК/НЕЙТ/ППД/ПРД)
indicators/analysis.py      # поддержка/сопротивление, Fibonacci, объёмный профиль, risk/reward
indicators/backtest.py      # бэктест Score: одна пара порогов, grid-search, walk-forward
ui/watchlist.py             # таблица watchlist + селекторы + алерты
ui/alerts.py                 # алерты на вход Score/Прогноза в зону ПОК/ПРД
ui/backtest_lab.py          # расширенная проверка сигнала (grid-search/walk-forward/сводка по всем монетам)
ui/analysis_sidebar.py      # боковая панель структурного анализа + бэктест по клику на монету
ui/chart.py                 # график выбранной монеты
```

Это не финансовая рекомендация и не готовая торговая система — набор
индикаторов и фильтров для собственного анализа.